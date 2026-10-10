"""Commander agent: coordinates and requests modes; never approves (the deterministic gate does)."""
from __future__ import annotations

from typing import Any

from ares.models import COMMANDER, AgentContext, CommanderTurn
from ares.agents import analysis as A
from ares.agents import prompts, rule_policy, sanitize
from ares.agents.base import AgentBase, short_reason
from ares.agents.profiles import PROFILES


class CommanderAgent(AgentBase):
    def __init__(self, provider: Any | None, provider_error: str | None = None):
        super().__init__(COMMANDER, PROFILES[COMMANDER], provider, provider_error)
        self.state.update({"scenarios": {}})

    def take_turn(self, ctx: AgentContext) -> CommanderTurn:
        skip = self.skip_reason(ctx)
        turn: CommanderTurn | None = None
        if skip is None:
            try:
                sc = rule_policy.observe_transcript(self.state, ctx)
                usable = [c for c in (ctx.candidates or []) if ctx.override_available or not c.get("requires_override")]
                ranked = sorted(usable, key=lambda c: rule_policy.score_candidate(c, ctx, sc))
                system, user = prompts.commander_prompt(self, ctx, ranked, sc)
                raw = self.ask_json(system, user, sanitize.commander_raw_ok, self.call_budget(ctx))
                turn = sanitize.commander_turn(raw, self, ctx, sc)
                meta = self.llm_meta()
                turn.source, turn.provider, turn.model, turn.fallback_reason = (
                    meta["source"], meta["provider"], meta["model"], None)
            except Exception as exc:
                skip = short_reason(exc)
                turn = None
        if turn is None:
            turn = rule_policy.commander_turn(self, ctx)
            turn.source, turn.fallback_reason, turn.provider, turn.model = "rule-based", skip, None, None
        target = A.combo_label(turn.target_combo) if turn.target_combo else ("INFEASIBLE" if turn.declare_infeasible
                                                                             else "-")
        self.remember(ctx, "turn", saw=f"plan v{A.plan_version(ctx)} {(ctx.plan or {}).get('status', '-')}; "
                      f"{len(ctx.candidates or [])} candidates", said=turn.message,
                      decided=f"target {target}; sacrifices {','.join(turn.sacrifice_candidates) or 'none'}",
                      source=turn.source)
        return turn
