"""LLM provider adapters (plain ``requests``; API keys only from environment variables).

Free-tier research (checked 2026-10-10; quotas change often and are NEVER guaranteed):

* Google Gemini API (AI Studio key, header ``x-goog-api-key``)
  - https://ai.google.dev/gemini-api/docs/pricing (last updated 2026-10-09): "Free of charge" tier listed for
    gemini-3.8-flash, gemini-3.6-flash, gemini-3.5-flash-lite, gemini-3.1-flash-lite, gemini-2.5-flash(-lite), ...
  - https://ai.google.dev/gemini-api/docs/models (2026-10-09): access to the 2.5 models is being limited to
    projects that used them before -> default to a current 3.x flash-lite model.
  - https://ai.google.dev/gemini-api/docs/rate-limits (2026-10-09): per-model free-tier RPM/TPM/RPD are only shown
    in AI Studio (https://aistudio.google.com/rate-limit); we pace at 4 s/call (~15 RPM) by default.
  - JSON mode: ``generationConfig.responseMimeType = "application/json"``.
* Groq (OpenAI-compatible, https://api.groq.com/openai/v1/chat/completions)
  - https://console.groq.com/docs/rate-limits (fetched 2026-10-10), free plan: openai/gpt-oss-120b and
    openai/gpt-oss-20b and qwen/qwen3.8-27b = 30 RPM, 1K RPD, 8K TPM, 200K TPD. 8K TPM is the binding limit
    (~4-5 of our ~1.5K-token calls per minute); 429s are retried (Retry-After honoured) then fall back.
  - https://console.groq.com/docs/structured-outputs: JSON Object Mode (``response_format: json_object``) is
    supported by all models; the prompt must mention JSON (ours does).
* OpenRouter (https://openrouter.ai/api/v1/chat/completions)
  - https://openrouter.ai/docs/api-reference/limits: ``:free`` models = 20 RPM; 50 requests/day with < $10 credits
    purchased, 1,000/day with >= $10.
  - https://openrouter.ai/docs/guides/routing/routers/free-router: ``openrouter/free`` routes to a random free model
    that supports the requested features (incl. structured outputs). Individual ``:free`` ids rotate frequently,
    so the router is the default. Only ``:free`` ids or ``openrouter/free`` are accepted unless
    OPENROUTER_ALLOW_PAID=1.
* Ollama (local, no key): OpenAI-compatible ``/v1/chat/completions`` with ``response_format: json_object``.

Env vars: GEMINI_API_KEY (or GOOGLE_API_KEY), GEMINI_MODEL, GROQ_API_KEY, GROQ_MODEL, OPENROUTER_API_KEY,
OPENROUTER_MODEL, OPENROUTER_ALLOW_PAID, OLLAMA_BASE_URL, OLLAMA_MODEL, ARES_LLM_TIMEOUT_S (default 25, budget per
call incl. retries), ARES_LLM_MAX_RETRIES (default 2), ARES_LLM_MIN_INTERVAL_S (> 0 overrides every provider's
pacing; 0/empty = per-provider default), ARES_<PROVIDER>_MIN_INTERVAL_S (explicit per provider, 0 allowed).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests

from ares.llm.base import (LLMError, LLMHTTPError, LLMJSONError, default_timeout_s, env_float, post_json)
from ares.llm.jsonutil import extract_json

_ENV_LOADED = False


def _load_env() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True
    try:
        from dotenv import load_dotenv
    except Exception:  # pragma: no cover - optional dependency
        return
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


SPECS: dict[str, dict[str, Any]] = {
    "gemini": {
        "key_envs": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "model_env": "GEMINI_MODEL",
        "default_model": "gemini-3.5-flash-lite",
        "fallback_models": ("gemini-3.1-flash-lite", "gemini-2.5-flash-lite", "gemini-2.5-flash"),
        "min_interval_s": 4.0,
        "note": "Google AI Studio free tier; per-model limits shown in AI Studio (paced 4s/call).",
    },
    "groq": {
        "key_envs": ("GROQ_API_KEY",),
        "model_env": "GROQ_MODEL",
        "default_model": "openai/gpt-oss-120b",
        "fallback_models": ("openai/gpt-oss-20b", "qwen/qwen3.8-27b"),
        "min_interval_s": 2.5,
        "note": "Groq free plan: 30 RPM, 1K RPD, 8K TPM (TPM is the binding limit).",
    },
    "openrouter": {
        "key_envs": ("OPENROUTER_API_KEY",),
        "model_env": "OPENROUTER_MODEL",
        "default_model": "openrouter/free",
        "fallback_models": ("openai/gpt-oss-20b:free",),
        "min_interval_s": 3.5,
        "note": "OpenRouter :free models: 20 RPM, 50 req/day (<$10 credits) or 1,000/day.",
    },
    "ollama": {
        "key_envs": (),
        "model_env": "OLLAMA_MODEL",
        "default_model": "llama3.1:8b",
        "fallback_models": (),
        "min_interval_s": 0.0,
        "note": "Local Ollama server (no key, no quota).",
    },
}


def _min_interval(name: str) -> float:
    explicit = os.environ.get(f"ARES_{name.upper()}_MIN_INTERVAL_S", "")
    if explicit.strip():
        return max(0.0, env_float(f"ARES_{name.upper()}_MIN_INTERVAL_S", 0.0))
    glob = env_float("ARES_LLM_MIN_INTERVAL_S", 0.0)
    return glob if glob > 0 else float(SPECS[name]["min_interval_s"])


def _key_for(name: str) -> tuple[str | None, str]:
    envs = SPECS[name]["key_envs"]
    for env in envs:
        val = os.environ.get(env, "").strip()
        if val:
            return val, env
    return None, (envs[0] if envs else "")


def _ollama_base() -> str:
    return (os.environ.get("OLLAMA_BASE_URL", "").strip() or "http://localhost:11434").rstrip("/")


def _ollama_models(timeout: float = 1.0) -> list[str] | None:
    """Installed model names, or None if the server is unreachable."""
    try:
        r = requests.get(f"{_ollama_base()}/api/tags", timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        return [m.get("name", "") for m in data.get("models", []) if isinstance(m, dict) and m.get("name")]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Provider classes
# ---------------------------------------------------------------------------
class _BaseProvider:
    name = "base"

    def __init__(self, model: str, api_key: str | None = None, fallback_models: tuple[str, ...] = (),
                 min_interval_s: float | None = None, max_retries: int | None = None):
        self.model = model
        self._api_key = api_key
        self.fallback_models = tuple(m for m in fallback_models if m and m != model)
        self.min_interval_s = _min_interval(self.name) if min_interval_s is None else min_interval_s
        self.max_retries = max_retries

    def __repr__(self) -> str:  # never show the key
        return f"<{type(self).__name__} model={self.model!r}>"

    def _request_text(self, model: str, system: str, user: str, timeout_s: float) -> str:
        raise NotImplementedError

    def complete_json(self, system: str, user: str, timeout_s: float | None = None) -> dict:
        import time
        budget = float(timeout_s) if timeout_s else default_timeout_s()
        deadline = time.monotonic() + budget
        last_err: LLMError | None = None
        for model in (self.model,) + self.fallback_models:
            remaining = deadline - time.monotonic()
            if remaining < 1.0:
                break
            try:
                text = self._request_text(model, system, user, remaining)
            except LLMHTTPError as exc:
                if exc.status == 404:          # model id retired/unknown -> try the next free default
                    last_err = exc
                    continue
                raise
            if model != self.model:
                self.model = model             # keep labels honest: report the model that answered
            return extract_json(text)
        raise last_err or LLMError(f"{self.name}: call budget exhausted")

    def _post(self, url: str, headers: dict[str, str], payload: dict[str, Any], timeout_s: float) -> dict:
        return post_json(self.name, url, headers, payload, timeout_s, self.max_retries, self.min_interval_s)


class GeminiProvider(_BaseProvider):
    name = "gemini"
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def _request_text(self, model: str, system: str, user: str, timeout_s: float) -> str:
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.7, "responseMimeType": "application/json"},
        }
        headers = {"x-goog-api-key": self._api_key or "", "Content-Type": "application/json"}
        data = self._post(f"{self.BASE_URL}/{model}:generateContent", headers, payload, timeout_s)
        cands = data.get("candidates") or []
        if not cands:
            block = (data.get("promptFeedback") or {}).get("blockReason")
            raise LLMError(f"gemini: no candidates{' (blocked: ' + str(block) + ')' if block else ''}")
        parts = ((cands[0] or {}).get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
        if not text.strip():
            raise LLMJSONError(f"gemini: empty reply (finishReason={cands[0].get('finishReason')})")
        return text


class OpenAICompatProvider(_BaseProvider):
    """Groq / OpenRouter / Ollama share the OpenAI chat-completions shape."""

    def __init__(self, name: str, url: str, model: str, api_key: str | None = None, **kw: Any):
        self.name = name
        self.url = url
        super().__init__(model, api_key, **kw)

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        if self.name == "openrouter":
            h["HTTP-Referer"] = "http://localhost:8501"
            h["X-Title"] = "ARES Accord"
        return h

    def _request_text(self, model: str, system: str, user: str, timeout_s: float) -> str:
        import time
        t0 = time.monotonic()
        payload: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.7,
            "response_format": {"type": "json_object"},
        }
        if self.name == "groq" and "gpt-oss" in model:
            payload["reasoning_effort"] = "low"
        try:
            data = self._post(self.url, self._headers(), payload, timeout_s)
        except LLMHTTPError as exc:
            msg = str(exc).lower()
            if exc.status == 400 and ("response_format" in msg or "json" in msg or "reasoning" in msg):
                payload.pop("response_format", None)      # model without JSON mode: rely on prompt + extractor
                payload.pop("reasoning_effort", None)
                data = self._post(self.url, self._headers(), payload, max(1.0, timeout_s - (time.monotonic() - t0)))
            else:
                raise
        if data.get("error"):
            err = data["error"]
            raise LLMError(f"{self.name}: {str(err.get('message') if isinstance(err, dict) else err)[:160]}")
        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"{self.name}: no choices in reply")
        content = ((choices[0] or {}).get("message") or {}).get("content")
        if not content or not str(content).strip():
            raise LLMJSONError(f"{self.name}: empty reply")
        return str(content)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
RULE_BASED = {"name": "rule-based", "model": None, "available": True, "env_var": None,
              "note": "Deterministic fallback policy - not an LLM"}


def _configured_model(name: str) -> str:
    spec = SPECS[name]
    return os.environ.get(spec["model_env"], "").strip() or spec["default_model"]


def available_providers() -> list[dict]:
    _load_env()
    out: list[dict] = [dict(RULE_BASED)]
    for name in ("gemini", "groq", "openrouter"):
        key, env = _key_for(name)
        model = _configured_model(name)
        note = SPECS[name]["note"]
        if name == "openrouter" and not _openrouter_model_ok(model):
            note = f"Model '{model}' is not a :free variant; set OPENROUTER_ALLOW_PAID=1 to allow. " + note
        out.append({"name": name, "model": model, "available": bool(key) and (
            name != "openrouter" or _openrouter_model_ok(model)), "env_var": env, "note": note})
    models = _ollama_models(timeout=1.0)
    model = _configured_model("ollama")
    if models and not os.environ.get("OLLAMA_MODEL", "").strip() and model not in models:
        model = models[0]
    note = SPECS["ollama"]["note"] if models is not None else f"Ollama not reachable at {_ollama_base()}"
    out.append({"name": "ollama", "model": model, "available": bool(models), "env_var": "OLLAMA_BASE_URL",
                "note": note if models != [] else "Ollama reachable but no models pulled"})
    return out


def _openrouter_model_ok(model: str) -> bool:
    if os.environ.get("OPENROUTER_ALLOW_PAID", "").strip() in ("1", "true", "yes"):
        return True
    return model.endswith(":free") or model == "openrouter/free"


def get_provider(name: str | None) -> _BaseProvider | None:
    """Return a provider instance, None for rule-based, or raise LLMError (missing key / unknown)."""
    _load_env()
    n = (name or "rule-based").strip().lower()
    if n in ("rule-based", "rule_based", "rules", "rule", "none", "fallback", ""):
        return None
    if n not in SPECS:
        raise LLMError(f"unknown provider '{name}'")
    spec = SPECS[n]
    model = _configured_model(n)
    if n == "ollama":
        models = _ollama_models(timeout=1.0)
        if models is None:
            raise LLMError(f"Ollama not reachable at {_ollama_base()}")
        if models and not os.environ.get("OLLAMA_MODEL", "").strip() and model not in models:
            model = models[0]
        return OpenAICompatProvider("ollama", f"{_ollama_base()}/v1/chat/completions", model)
    key, env = _key_for(n)
    if not key:
        raise LLMError(f"{env} is not set (provider '{n}' unavailable)")
    if n == "gemini":
        return GeminiProvider(model, key, fallback_models=spec["fallback_models"])
    if n == "groq":
        return OpenAICompatProvider("groq", "https://api.groq.com/openai/v1/chat/completions", model, key,
                                    fallback_models=spec["fallback_models"])
    if not _openrouter_model_ok(model):
        raise LLMError(f"OpenRouter model '{model}' is not a :free variant (set OPENROUTER_ALLOW_PAID=1 to allow)")
    fallbacks = tuple(m for m in spec["fallback_models"] if _openrouter_model_ok(m))
    return OpenAICompatProvider("openrouter", "https://openrouter.ai/api/v1/chat/completions", model, key,
                                fallback_models=fallbacks)
