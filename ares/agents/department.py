"""Department agents (Life Support, Medical, Food Production, Engineering)."""
from __future__ import annotations

from typing import Any

from ares.models import DEPARTMENTS, AgentContext, DepartmentTurn, Vote
from ares.agents import analysis as A
from ares.agents import prompts, rule_policy, sanitize
from ares.agents.base import AgentBase, short_reason
from ares.agents.profiles import PROFILES


class DepartmentAgent(AgentBase):
    def __init__(self, dept: str, provider: Any | None, provider_error: str | None = None):
        if dept not in DEPARTMENTS:
            raise ValueError(f"unknown department {dept!r}")
        super().__init__(dept, PROFILES[dept], provider, provider_error)
        self.state.update({"last_mode": None, "refusals": {}, "grievances": [], "commitments_given": [],
                           "objected": [], "votes": []})

    # ------------------------------------------------------------------ turn
    def take_turn(self, ctx: AgentContext) -> DepartmentTurn:
        skip = self.skip_reason(ctx)
        turn: DepartmentTurn | None = None
        if skip is None:
            try:
                system, user = prompts.department_prompt(self, ctx)
                raw = self.ask_json(system, user, sanitize.department_raw_ok, self.call_budget(ctx))
                turn = sanitize.department_turn(raw, self, ctx)
                meta = self.llm_meta()
                turn.source, turn.provider, turn.model, turn.fallback_reason = (
                    meta["source"], meta["provider"], meta["model"], None)
                if turn.refuses_sacrifice:
                    sid = str(ctx.scenario_id)
                    self.state["refusals"][sid] = int(self.state["refusals"].get(sid, 0)) + 1
            except Exception as exc:  # LLMError, timeout, invalid JSON, unexpected shape
                skip = short_reason(exc)
                turn = None
        if turn is None:
            turn = rule_policy.department_turn(self, ctx)
            turn.source, turn.fallback_reason, turn.provider, turn.model = "rule-based", skip, None, None
        self.state["last_mode"] = turn.mode
        for o in turn.offers:
            if o.template_id:
                self.state["commitments_given"].append(o.template_id)
        req = (ctx.commander_requests or {}).get(self.agent_id)
        self.remember(ctx, "turn", saw=f"plan v{A.plan_version(ctx)} {(ctx.plan or {}).get('status', '-')}; "
                      f"asked {req or '-'}; offers to me {len(A.distinct_owner_offers(ctx, self.agent_id))}",
                      said=turn.message, decided=f"{turn.msg_type} {turn.mode}", source=turn.source)
        return turn

    # ------------------------------------------------------------------ vote
    def vote(self, ctx: AgentContext) -> Vote:
        skip = self.skip_reason(ctx)
        v: Vote | None = None
        if skip is None:
            try:
                system, user = prompts.vote_prompt(self, ctx)
                raw = self.ask_json(system, user, sanitize.vote_raw_ok, self.call_budget(ctx))
                v = sanitize.vote(raw, self, ctx)
                meta = self.llm_meta()
                v.source, v.fallback_reason, v.provider, v.model = "llm", None, meta["provider"], meta["model"]
            except Exception as exc:
                skip = short_reason(exc)
                v = None
        if v is None:
            v = rule_policy.department_vote(self, ctx)
            v.source, v.fallback_reason = "rule-based", skip
        v.plan_version = A.plan_version(ctx)
        self.state["votes"].append([ctx.scenario_id, v.plan_version, v.decision])
        del self.state["votes"][:-30]
        self.remember(ctx, "vote", saw=f"plan v{v.plan_version} {A.combo_label(A.plan_modes(ctx))}",
                      said=v.reason, decided=f"VOTE {v.decision} v{v.plan_version}", source=v.source)
        return v

    def reconsider_vote(self, ctx: AgentContext, original: Vote, corrections: list[str]) -> Vote:
        """Second, recorded vote on the SAME version after the validator showed a factual error in `original`.

        The agent decides again (LLM, or the labelled rule policy on failure); it may keep REJECT.
        """
        skip = self.skip_reason(ctx)
        v: Vote | None = None
        if skip is None:
            try:
                system, user = prompts.reconsider_prompt(self, ctx, original.reason, corrections)
                raw = self.ask_json(system, user, sanitize.vote_raw_ok, self.call_budget(ctx))
                v = sanitize.vote(raw, self, ctx)
                meta = self.llm_meta()
                v.source, v.fallback_reason, v.provider, v.model = "llm", None, meta["provider"], meta["model"]
            except Exception as exc:
                skip = short_reason(exc)
                v = None
        if v is None:
            v = rule_policy.department_vote(self, ctx)
            v.source, v.fallback_reason = "rule-based", skip
        v.plan_version = A.plan_version(ctx)
        v.reconsidered = True
        self.state["votes"].append([ctx.scenario_id, v.plan_version, f"{v.decision} (reconsidered)"])
        del self.state["votes"][:-30]
        self.remember(ctx, "reconsider", saw=f"fact check on my REJECT of v{v.plan_version}: "
                      f"{A.short('; '.join(corrections), 120)}", said=v.reason,
                      decided=f"VOTE {v.decision} v{v.plan_version} (reconsidered)", source=v.source)
        return v
