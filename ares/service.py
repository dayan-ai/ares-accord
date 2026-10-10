"""Facade used by the Streamlit UI. The UI must not touch storage or agents directly."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Callable

from ares import events, exports
from ares.config import Settings, load_settings
from ares.models import BASELINE_POOL
from ares.orchestrator import Orchestrator
from ares.storage import Store

RULE_BASED = {"name": "rule-based", "model": "-", "available": True, "env_var": None,
              "note": "Deterministic fallback policy - not an LLM"}


class Service:
    def __init__(self, settings: Settings | None = None, store: Store | None = None,
                 agent_factory: Callable[[str], Any] | None = None):
        self.settings = settings or load_settings()
        self.store = store or Store(self.settings.db_path)
        self.orch = Orchestrator(self.store, self.settings, agent_factory=agent_factory)
        self._run_lock = threading.Lock()

    @contextmanager
    def _exclusive(self):
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("A negotiation is already running. Wait for it to finish.")
        try:
            yield
        finally:
            self._run_lock.release()

    # -- providers ---------------------------------------------------------
    def providers(self) -> list[dict[str, Any]]:
        try:
            from ares.llm import available_providers
            providers = available_providers()
        except Exception as exc:  # LLM layer unavailable -> rule-based only
            return [{**RULE_BASED, "note": f"{RULE_BASED['note']} (LLM layer unavailable: {exc})"}]
        if not any(p.get("name") == "rule-based" for p in providers):
            providers = [RULE_BASED, *providers]
        return providers

    def _check_provider(self, provider: str) -> str:
        provider = provider or self.settings.default_provider
        if provider == "rule-based":
            return provider
        match = next((p for p in self.providers() if p.get("name") == provider), None)
        if match is None:
            raise ValueError(f"Unknown provider '{provider}'.")
        if not match.get("available"):
            raise ValueError(f"Provider '{provider}' is not configured ({match.get('note') or 'missing key'}). "
                             f"Set {match.get('env_var') or 'its API key'} in .env or choose 'rule-based'.")
        return provider

    # -- runs --------------------------------------------------------------
    def start_baseline(self, pool: dict[str, int] | None, provider: str, max_rounds: int = 6,
                       on_message: Callable[[dict], None] | None = None,
                       require_cosign: bool = False) -> dict[str, Any]:
        provider = self._check_provider(provider)
        with self._exclusive():
            return self.orch.start_baseline(dict(pool or BASELINE_POOL), provider, max_rounds, on_message,
                                            require_cosign=require_cosign)

    def inject_event(self, event: dict[str, Any] | str, provider: str, max_rounds: int = 5,
                     on_message: Callable[[dict], None] | None = None,
                     require_cosign: bool = False) -> dict[str, Any]:
        provider = self._check_provider(provider)
        with self._exclusive():
            return self.orch.inject_event(event, provider, max_rounds, on_message, require_cosign=require_cosign)

    def cosign(self, approve: bool, note: str = "") -> dict[str, Any]:
        """Judge co-sign / veto for a plan that already passed the deterministic approval gate."""
        with self._exclusive():
            return self.orch.cosign(approve, note)

    def current_pool(self) -> dict[str, int]:
        session = self.store.current_session()
        scenario = self.store.latest_scenario(session["id"]) if session else None
        return dict(scenario["pool"]) if scenario else dict(BASELINE_POOL)

    def preview_event(self, event: dict[str, Any] | str) -> dict[str, Any]:
        ev = events.parse_event(event)
        pool = self.current_pool()
        new_pool, changes = events.apply_event(pool, ev)
        return {"event": ev.to_dict(), "pool": pool, "new_pool": new_pool, "changes": changes}

    # -- session management ------------------------------------------------
    def reset_session(self) -> None:
        with self._exclusive():
            self.store.new_session()

    def wipe_all(self) -> None:
        with self._exclusive():
            self.store.wipe()

    # -- read models -------------------------------------------------------
    def _plan_with_votes(self, plan: dict[str, Any] | None) -> dict[str, Any] | None:
        if not plan:
            return None
        return {**plan, "votes": self.store.votes(plan["id"])}

    def state(self) -> dict[str, Any]:
        session = self.store.current_session()
        scenario = self.store.latest_scenario(session["id"]) if session else None
        plans = [self._plan_with_votes(p) for p in self.store.plans(scenario["id"])] if scenario else []
        approved = self.store.latest_approved_plan(session["id"]) if session else None
        protocol = (scenario or {}).get("protocol") or {
            "rounds_before_approval": 0, "revised_or_rejected": False, "sacrifice_refused": False,
            "return_commitments": 0, "rounds": (scenario or {}).get("round", 0)}
        return {
            "session": session,
            "scenario": scenario,
            "plan": plans[-1] if plans else None,
            "approved_plan": self._plan_with_votes(approved),
            "plans": plans,
            "commitments": [c.to_dict() for c in self.store.commitments(scenario["id"])] if scenario else [],
            "scenarios": self.store.scenarios(),
            "sessions": self.store.sessions(),
            "protocol": protocol,
            "running": self._run_lock.locked(),
        }

    def transcript(self, session_id: int | None = None, scenario_id: int | None = None) -> list[dict[str, Any]]:
        return self.store.messages(session_id, scenario_id)

    def export_bundle(self, session_id: int | None = None) -> dict[str, str]:
        if session_id is None:
            session = self.store.current_session()
            session_id = session["id"] if session else None
        return exports.build_bundle(self.store, session_id)


_SERVICE: Service | None = None


def get_service() -> Service:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = Service()
    return _SERVICE
