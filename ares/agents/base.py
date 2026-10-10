"""Shared agent plumbing: private memory/state, LLM call with one repair retry, time budget."""
from __future__ import annotations

import copy
import time
from typing import Any, Callable

from ares.llm.base import LLMError, LLMJSONError, default_timeout_s
from ares.models import AgentContext
from ares.agents.prompts import REPAIR_SUFFIX
from ares.agents import analysis as A

NO_LLM_REASON = "rule-based mode (no LLM provider)"
BUDGET_REASON = "scenario time budget nearly exhausted"
MEMORY_CAP = 60


class AgentBase:
    agent_id: str = ""

    def __init__(self, agent_id: str, profile: dict, provider: Any | None, provider_error: str | None = None):
        self.agent_id = agent_id
        self.profile = copy.deepcopy(profile)
        self.provider = provider
        self.provider_error = provider_error
        self.memory: list[dict] = []
        self.state: dict[str, Any] = {}
        self._last_sanitize_notes: list[str] = []

    # ------------------------------------------------------------- persistence
    def export_state(self) -> dict:
        return {"agent_id": self.agent_id, "memory": copy.deepcopy(self.memory), "state": copy.deepcopy(self.state)}

    def load_state(self, d: dict) -> None:
        if not isinstance(d, dict):
            return
        self.memory = list(copy.deepcopy(d.get("memory") or []))[-MEMORY_CAP:]
        self.state = dict(copy.deepcopy(d.get("state") or {}))

    def remember(self, ctx: AgentContext, kind: str, saw: str, said: str, decided: str, source: str) -> None:
        self.memory.append({"scenario": ctx.scenario_id, "round": ctx.round_no, "phase": ctx.phase, "kind": kind,
                            "saw": A.short(saw, 200), "said": A.short(said, 240), "decided": A.short(decided, 120),
                            "source": source})
        del self.memory[:-MEMORY_CAP]

    # ------------------------------------------------------------- LLM helpers
    def skip_reason(self, ctx: AgentContext) -> str | None:
        """Why this turn must be rule-based, or None if the LLM may be called."""
        if self.provider is None:
            return self.provider_error or NO_LLM_REASON
        tb = getattr(ctx, "time_budget_s", None)
        if tb is not None and tb < 4:
            return BUDGET_REASON
        return None

    def call_budget(self, ctx: AgentContext) -> float:
        budget = default_timeout_s()
        tb = getattr(ctx, "time_budget_s", None)
        if tb is not None:
            budget = min(budget, max(1.0, tb - 2))
        return budget

    def ask_json(self, system: str, user: str, ok: Callable[[Any], bool], budget: float) -> dict:
        """One LLM call plus ONE repair retry for invalid JSON; raises LLMError otherwise."""
        t0 = time.monotonic()
        try:
            raw = self.provider.complete_json(system, user, budget)
            if ok(raw):
                return raw
            problem = "JSON missing required fields"
        except LLMJSONError as exc:
            problem = str(exc)
        remaining = budget - (time.monotonic() - t0)
        if remaining < 2.0:
            raise LLMError(f"invalid JSON ({A.short(problem, 60)}); no time for repair retry")
        try:
            raw = self.provider.complete_json(system, user + REPAIR_SUFFIX, remaining)
        except LLMJSONError as exc:
            raise LLMError(f"invalid JSON after repair retry ({A.short(str(exc), 60)})")
        if ok(raw):
            return raw
        raise LLMError("invalid JSON after repair retry (missing required fields)")

    def llm_meta(self) -> dict:
        return {"source": "llm", "provider": getattr(self.provider, "name", None),
                "model": getattr(self.provider, "model", None), "fallback_reason": None}


def short_reason(exc: BaseException) -> str:
    if isinstance(exc, LLMError):
        return A.short(str(exc), 140) or "LLM error"
    return A.short(f"internal error: {type(exc).__name__}: {exc}", 140)
