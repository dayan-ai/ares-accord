"""LLM base layer: errors, Provider protocol, and a paced, bounded-retry HTTP helper.

Every request has a strict timeout; the whole ``complete_json`` call (pacing waits,
retries, backoff) is bounded by its ``timeout_s`` budget so one agent turn can never
stall the scenario. Keys are passed in headers only and never logged or echoed.
"""
from __future__ import annotations

import os
import random
import threading
import time
from typing import Any, Protocol, runtime_checkable

import requests


class LLMError(Exception):
    """Any provider failure (HTTP, timeout, quota, empty or invalid reply)."""


class LLMHTTPError(LLMError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class LLMJSONError(LLMError):
    """The provider answered but the reply was not a usable JSON object."""


@runtime_checkable
class Provider(Protocol):
    name: str
    model: str

    def complete_json(self, system: str, user: str, timeout_s: float) -> dict: ...


# ---------------------------------------------------------------------------
# Environment helpers
# ---------------------------------------------------------------------------
def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "")
    try:
        return float(raw) if raw.strip() else default
    except ValueError:
        return default


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw) if raw.strip() else default
    except ValueError:
        return default


def default_timeout_s() -> float:
    return max(2.0, env_float("ARES_LLM_TIMEOUT_S", 25.0))


def default_max_retries() -> int:
    return max(0, min(5, env_int("ARES_LLM_MAX_RETRIES", 2)))


# ---------------------------------------------------------------------------
# Per-provider pacing + circuit breaker (thread-safe)
# ---------------------------------------------------------------------------
RETRY_AFTER_CAP_S = 20.0
CIRCUIT_FAILURES = 3          # consecutive failed calls before cooling down
CIRCUIT_COOLDOWN_S = 45.0     # during cooldown calls fail fast -> rule-based fallback


class _ProviderState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.meta = threading.Lock()
        self.last_call = 0.0
        self.failures = 0
        self.cooldown_until = 0.0


_STATES: dict[str, _ProviderState] = {}
_STATES_LOCK = threading.Lock()


def _state(name: str) -> _ProviderState:
    with _STATES_LOCK:
        st = _STATES.get(name)
        if st is None:
            st = _STATES[name] = _ProviderState()
        return st


def reset_provider_state() -> None:
    """Forget pacing/circuit state (tests, session reset)."""
    with _STATES_LOCK:
        _STATES.clear()


def _record_ok(st: _ProviderState) -> None:
    with st.meta:
        st.failures = 0
        st.cooldown_until = 0.0


def _record_fail(st: _ProviderState) -> None:
    with st.meta:
        st.failures += 1
        if st.failures >= CIRCUIT_FAILURES:
            st.cooldown_until = time.monotonic() + CIRCUIT_COOLDOWN_S


def _pace(st: _ProviderState, min_interval: float, deadline: float) -> None:
    if min_interval <= 0:
        return
    with st.lock:
        now = time.monotonic()
        wait = st.last_call + min_interval - now
        if wait > 0:
            if now + wait > deadline - 0.5:
                raise LLMError(f"rate pacing ({min_interval:.1f}s between calls) exceeds the call budget")
            time.sleep(wait)
        st.last_call = time.monotonic()


def _retry_after(resp: Any) -> float | None:
    try:
        raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    except Exception:
        return None
    if raw is None:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def short_error(resp: Any) -> str:
    """Short, key-free error description from an HTTP response."""
    msg = ""
    try:
        data = resp.json()
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            msg = str(err.get("message") or err.get("status") or "")
        elif err:
            msg = str(err)
    except Exception:
        try:
            msg = resp.text or ""
        except Exception:
            msg = ""
    msg = " ".join(msg.split())
    return msg[:160]


def _backoff(attempt: int) -> float:
    return min(8.0, 1.0 * (2 ** attempt)) + random.uniform(0.0, 0.3)


def post_json(name: str, url: str, headers: dict[str, str], payload: dict[str, Any], timeout_s: float,
              max_retries: int | None = None, min_interval_s: float = 0.0) -> dict[str, Any]:
    """POST JSON with pacing, strict timeouts and bounded retries on 429/5xx/timeouts.

    The total wall time (pacing + attempts + backoff) is bounded by ``timeout_s``.
    Raises LLMError / LLMHTTPError; never includes request headers in messages.
    """
    st = _state(name)
    now = time.monotonic()
    if st.cooldown_until > now:
        raise LLMError(f"{name} cooling down after repeated failures ({st.cooldown_until - now:.0f}s left)")
    retries = default_max_retries() if max_retries is None else max(0, max_retries)
    deadline = now + max(1.0, float(timeout_s))
    attempts = retries + 1
    last = "no attempt made"
    for attempt in range(attempts):
        remaining = deadline - time.monotonic()
        if remaining < 0.5:
            last = "call budget exhausted"
            break
        _pace(st, min_interval_s, deadline)
        remaining = deadline - time.monotonic()
        if remaining < 0.5:
            last = "call budget exhausted"
            break
        delay = _backoff(attempt)
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=(min(5.0, remaining), remaining))
        except requests.Timeout:
            last = f"timeout after {remaining:.0f}s"
        except requests.RequestException as exc:
            last = f"connection error ({type(exc).__name__})"
        else:
            status = resp.status_code
            if status == 200:
                try:
                    data = resp.json()
                except ValueError:
                    _record_fail(st)
                    raise LLMError(f"{name}: HTTP 200 with non-JSON body")
                if not isinstance(data, dict):
                    _record_fail(st)
                    raise LLMError(f"{name}: unexpected response shape")
                _record_ok(st)
                return data
            if status == 429 or status >= 500:
                last = f"HTTP {status} {short_error(resp)}".strip()
                ra = _retry_after(resp)
                if ra is not None:
                    delay = min(ra, RETRY_AFTER_CAP_S)
            else:
                if status not in (400, 404, 422):
                    _record_fail(st)          # 401/403 etc.: config problem, fail fast next time
                raise LLMHTTPError(f"{name}: HTTP {status} {short_error(resp)}".strip(), status)
        if attempt < attempts - 1:
            if time.monotonic() + delay > deadline - 0.5:
                last += " (no budget left to retry)"
                break
            time.sleep(delay)
    _record_fail(st)
    raise LLMError(f"{name}: {last}")
