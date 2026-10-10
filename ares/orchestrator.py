"""Negotiation protocol runner.

The orchestrator owns the message bus, plan versioning, vote invalidation and the
approval decision. Agents only *speak*: they propose modes, object, offer or accept
commitments and vote. Every approval goes through `validator.approval_gate`, so the
Commander (LLM or rule-based) can never bypass the validator, the risk limits, the
return agreements or the votes.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ares import events, factcheck, optimizer, validator
from ares.config import Settings
from ares.models import (
    ACCEPT, AGENT_LABELS, BASELINE, COMMANDER, DEPARTMENTS, EVENT, MODE_PACKAGES, REFUSE_SACRIFICE, REJECT,
    RESOURCE_LABELS, RESOURCES, RETURN_TEMPLATES, SACRIFICE, STANDARD, AgentContext, Commitment, CommitmentOffer,
    CommanderTurn, CrisisEvent, DepartmentTurn, Policy, ValidationResult, Vote, mode_code,
)
from ares.storage import Store, now

OnMessage = Callable[[dict[str, Any]], None] | None

BASELINE_NAME = "Baseline crisis: micrometeorite storm"
BASELINE_DESCRIPTION = (
    "A micrometeorite storm has damaged the solar field, opened a slow habitat leak, disrupted greenhouse "
    "cooling, and injured six crew members. The colony has 42 people."
)
LLM_GRACE_S = 20.0
COSIGN_RISK_THRESHOLD = 20  # starter checklist bonus: human approval if risk score > 20
_TEMPLATES_BY_ID = {t.template_id: t for ts in RETURN_TEMPLATES.values() for t in ts}


class _End(Exception):
    def __init__(self, status: str, reason: str, extra: dict[str, Any] | None = None):
        super().__init__(reason)
        self.status, self.reason, self.extra = status, reason, extra or {}


@dataclass
class _Run:
    session_id: int
    scenario_id: int
    kind: str
    name: str
    pool: dict[str, int]
    policy: Policy
    min_rounds: int
    max_rounds: int
    deadline: float
    provider: str
    on_message: OnMessage
    cosign: bool = False
    commander: Any = None
    depts: dict[str, Any] = field(default_factory=dict)
    event: CrisisEvent | None = None
    forbidden: list[str] = field(default_factory=list)
    round: int = 0
    declared: dict[str, str | None] = field(default_factory=lambda: {d: None for d in DEPARTMENTS})
    plan: dict[str, Any] | None = None
    validation: ValidationResult | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    infeasibility: dict[str, Any] | None = None
    sacrifice_requests: dict[str, int] = field(default_factory=dict)
    commander_requests: dict[str, str] = field(default_factory=dict)
    round_sacrifice_candidates: list[str] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)
    protocol: dict[str, Any] = field(default_factory=lambda: {
        "rounds_before_approval": 0, "revised_or_rejected": False, "sacrifice_refused": False,
        "return_commitments": 0, "rounds": 0,
    })


def _label(agent: str) -> str:
    return AGENT_LABELS.get(agent, agent)


def _combo_label(modes: dict[str, str | None]) -> str:
    return "+".join(modes.get(d) or "?" for d in DEPARTMENTS)


def _fmt_res(values: dict[str, int]) -> str:
    return " ".join(f"{RESOURCE_LABELS[r][0] if r != 'robot' else 'R'}{values.get(r, 0)}" for r in RESOURCES)


def _sanitize_pool(pool: dict[str, Any]) -> dict[str, int]:
    out = {}
    for r in RESOURCES:
        try:
            out[r] = max(0, int(pool.get(r, 0)))
        except (TypeError, ValueError):
            raise ValueError(f"Resource '{r}' must be a whole number, got {pool.get(r)!r}")
    return out


class Orchestrator:
    def __init__(self, store: Store, settings: Settings, agent_factory: Callable[[str], Any] | None = None):
        self.store = store
        self.settings = settings
        self._agent_factory = agent_factory

    # ------------------------------------------------------------------ public
    def start_baseline(self, pool: dict[str, Any], provider: str, max_rounds: int | None = None,
                       on_message: OnMessage = None, require_cosign: bool = False) -> dict[str, Any]:
        pool = _sanitize_pool(pool)
        max_rounds = max(self.settings.baseline_min_rounds, int(max_rounds or self.settings.baseline_max_rounds))
        session = self.store.current_session()
        if session is None or self.store.scenarios(session["id"]):
            session_id = self.store.new_session()
        else:
            session_id = session["id"]
        commander, depts = self._build_agents(provider, session_id)
        policy = Policy.baseline()
        sid = self.store.create_scenario(
            session_id=session_id, kind=BASELINE, name=BASELINE_NAME, description=BASELINE_DESCRIPTION,
            pool=pool, base_pool=pool, policy=policy.to_dict(), event=None, status="RUNNING",
            max_rounds=max_rounds, min_rounds=self.settings.baseline_min_rounds, provider=provider,
        )
        run = _Run(session_id=session_id, scenario_id=sid, kind=BASELINE, name=BASELINE_NAME, pool=pool,
                   policy=policy, min_rounds=self.settings.baseline_min_rounds, max_rounds=max_rounds,
                   deadline=time.monotonic() + self.settings.baseline_deadline_s, provider=provider,
                   on_message=on_message, commander=commander, depts=depts, cosign=require_cosign)
        run.protocol["cosign_required"] = require_cosign
        self._say(run, "JUDGE", "SYSTEM",
                  f"Judge started the baseline scenario with pool {_fmt_res(pool)} using provider '{provider}'.",
                  payload={"pool": pool, "provider": provider}, round_no=0)
        self._negotiate(run)
        return self.store.get_scenario(sid)

    def inject_event(self, event_input: dict[str, Any] | str, provider: str, max_rounds: int | None = None,
                     on_message: OnMessage = None, require_cosign: bool = False) -> dict[str, Any]:
        session = self.store.current_session()
        prev = self.store.latest_scenario(session["id"]) if session else None
        if prev is None:
            raise ValueError("Start the baseline crisis before injecting an event.")
        if prev["status"] == "RUNNING":
            raise ValueError("A negotiation is still running; wait for it to finish.")
        if prev["status"] == "AWAITING_COSIGN":
            raise ValueError("A plan is waiting for the judge co-sign. Co-sign or veto it before injecting an event.")
        ev = events.parse_event(event_input)
        new_pool, changes = events.apply_event(prev["pool"], ev)
        prev_forbidden = list((prev.get("event") or {}).get("cumulative_forbidden_modes", []))
        forbidden = sorted(set(prev_forbidden) | set(ev.forbidden_modes))
        max_rounds = max(self.settings.event_min_rounds, int(max_rounds or self.settings.event_max_rounds))
        session_id = session["id"]
        commander, depts = self._build_agents(provider, session_id)
        policy = Policy.baseline()
        ev_dict = ev.to_dict()
        ev_dict["changes"] = changes
        ev_dict["cumulative_forbidden_modes"] = forbidden
        sid = self.store.create_scenario(
            session_id=session_id, kind=EVENT, name=ev.name, description=ev.description, pool=new_pool,
            base_pool=prev["pool"], policy=policy.to_dict(), event=ev_dict, status="RUNNING",
            max_rounds=max_rounds, min_rounds=self.settings.event_min_rounds, provider=provider,
        )
        run = _Run(session_id=session_id, scenario_id=sid, kind=EVENT, name=ev.name, pool=new_pool,
                   policy=policy, min_rounds=self.settings.event_min_rounds, max_rounds=max_rounds,
                   deadline=time.monotonic() + self.settings.event_deadline_s, provider=provider,
                   on_message=on_message, commander=commander, depts=depts, event=ev, forbidden=forbidden,
                   cosign=require_cosign)
        run.protocol["cosign_required"] = require_cosign
        notes = f" Interpretation: {'; '.join(ev.interpretation_notes)}." if ev.interpretation_notes else ""
        self._say(run, "JUDGE", "EVENT",
                  f"EMERGENCY EVENT '{ev.name}': {ev.description or '(no description)'} "
                  f"Changes: {', '.join(changes) or 'no resource change'}."
                  + (f" Forbidden modes: {', '.join(forbidden)}." if forbidden else "") + notes,
                  payload={"event": ev_dict, "old_pool": prev["pool"], "new_pool": new_pool}, round_no=0)
        self._prepare_after_event(run, prev)
        self._negotiate(run)
        return self.store.get_scenario(sid)

    # ------------------------------------------------------------ agent setup
    def _build_agents(self, provider: str, session_id: int):
        if self._agent_factory is not None:
            commander, depts = self._agent_factory(provider)
        else:
            from ares.agents import build_agents  # imported lazily so storage-only use needs no LLM deps
            commander, depts = build_agents(provider)
        for agent in [commander, *depts.values()]:
            state = self.store.load_agent_state(session_id, getattr(agent, "agent_id", COMMANDER))
            if state and hasattr(agent, "load_state"):
                try:
                    agent.load_state(state)
                except Exception:  # corrupted memory must never block a run
                    pass
        return commander, depts

    def _save_agents(self, run: _Run) -> None:
        for agent in [run.commander, *run.depts.values()]:
            if agent is not None and hasattr(agent, "export_state"):
                try:
                    self.store.save_agent_state(run.session_id, getattr(agent, "agent_id", COMMANDER),
                                                agent.export_state())
                except Exception:
                    pass

    # ------------------------------------------------------------- messaging
    def _say(self, run: _Run, sender: str, msg_type: str, content: str, payload: dict[str, Any] | None = None,
             source: str = "system", provider: str | None = None, model: str | None = None,
             fallback_reason: str | None = None, recipient: str = "ALL", round_no: int | None = None) -> dict:
        msg = self.store.add_message({
            "session_id": run.session_id, "scenario_id": run.scenario_id,
            "round": run.round if round_no is None else round_no,
            "plan_version": run.plan["version"] if run.plan else None,
            "sender": sender, "recipient": recipient, "msg_type": msg_type, "content": content,
            "payload": payload or {}, "source": source, "provider": provider, "model": model,
            "fallback_reason": fallback_reason,
        })
        run.transcript.append({k: msg[k] for k in ("sender", "msg_type", "round", "plan_version", "content")})
        if run.on_message:
            try:
                run.on_message(msg)
            except Exception:  # UI callback errors must not break the negotiation
                pass
        return msg

    @staticmethod
    def _turn_meta(turn: Any) -> dict[str, Any]:
        return {"source": getattr(turn, "source", "llm") or "llm", "provider": getattr(turn, "provider", None),
                "model": getattr(turn, "model", None), "fallback_reason": getattr(turn, "fallback_reason", None)}

    # --------------------------------------------------------------- context
    def _ctx(self, run: _Run, phase: str) -> AgentContext:
        return AgentContext(
            scenario_id=run.scenario_id, scenario_kind=run.kind, round_no=run.round, max_rounds=run.max_rounds,
            min_rounds=run.min_rounds, phase=phase, pool=dict(run.pool), policy=run.policy,
            override_available=run.kind == EVENT, declared_modes=dict(run.declared),
            plan=dict(run.plan) if run.plan else None, validation=run.validation,
            candidates=[dict(c) for c in run.candidates], infeasibility=run.infeasibility,
            commitments=self.store.commitments(run.scenario_id),
            transcript=run.transcript[-self.settings.transcript_window:],
            sacrifice_requests=dict(run.sacrifice_requests), event=run.event,
            forbidden_modes=list(run.forbidden), commander_requests=dict(run.commander_requests),
            # soft LLM budget: ends LLM_GRACE_S before the hard deadline so the labelled rule-based
            # fallback can still finish the protocol instead of timing out
            time_budget_s=max(0.0, run.deadline - LLM_GRACE_S - time.monotonic()),
        )

    def _refresh_candidates(self, run: _Run) -> None:
        base = optimizer.enumerate_candidates(run.pool, run.policy, run.forbidden)
        for c in base:
            c["requires_override"] = False
        if run.kind == EVENT and not run.policy.crisis_override:
            labels = {c["label"] for c in base}
            for c in optimizer.enumerate_candidates(run.pool, Policy.override(), run.forbidden):
                if c["label"] not in labels:
                    c["requires_override"] = True
                    base.append(c)
        run.candidates = base
        if any(not c["requires_override"] for c in base):
            run.infeasibility = None
        else:
            run.infeasibility = optimizer.diagnose_infeasibility(
                run.pool, run.policy, run.forbidden, override_allowed=run.kind == EVENT)

    def _check_deadline(self, run: _Run) -> None:
        if time.monotonic() > run.deadline:
            raise _End("TIMEOUT", "Scenario time budget exhausted before a plan was approved.")

    # ------------------------------------------------------------ main loop
    def _negotiate(self, run: _Run) -> None:
        try:
            self._refresh_candidates(run)
            self._briefing(run)
            if not run.candidates:
                diag = optimizer.diagnose_infeasibility(run.pool, run.policy, run.forbidden,
                                                        override_allowed=run.kind == EVENT)
                raise _End("INFEASIBLE", self._infeasible_text(diag), {"diagnosis": diag})
            self._proposal_round(run)
            while run.round < run.max_rounds:
                self._negotiation_round(run)
            self._deadlock(run)
        except _End as end:
            self._finish(run, end)
        except Exception as exc:  # safe failure: record it, never approve
            self._say(run, "SYSTEM", "SYSTEM", f"Internal error stopped the negotiation safely: {exc!r}. "
                      "No plan was approved in this scenario.")
            self._finish(run, _End("ERROR", f"Internal error: {exc}"))
        finally:
            self._save_agents(run)

    def _briefing(self, run: _Run) -> None:
        pol = run.policy
        n_base = sum(1 for c in run.candidates if not c["requires_override"])
        n_over = sum(1 for c in run.candidates if c["requires_override"])
        lines = [
            f"CRISIS BRIEFING — {run.name}. Available pool: {_fmt_res(run.pool)}.",
            f"Limits: risk <= {pol.risk_limit}, at most {pol.max_sacrifices} Sacrifice mode(s); every sacrificing "
            "department needs 2 accepted return commitments from different agents.",
            f"Round limit {run.max_rounds}; approval needs validator PASS and 4 ACCEPT votes on the same plan "
            f"version, not before round {run.min_rounds}.",
        ]
        if run.kind == EVENT:
            lines.append("Crisis Override is available after this event (risk 28, up to 2 Sacrifice modes).")
        if run.forbidden:
            lines.append(f"Unavailable modes: {', '.join(run.forbidden)}.")
        lines.append("Departments: state your mode requests and what Restricted or Sacrifice would cost you.")
        self._say(run, COMMANDER, "BRIEFING", " ".join(lines), round_no=0, payload={
            "pool": run.pool, "policy": pol.to_dict(), "max_rounds": run.max_rounds, "min_rounds": run.min_rounds,
            "feasible_combinations": n_base, "feasible_with_override_only": n_over,
        })

    def _proposal_round(self, run: _Run) -> None:
        run.round = 1
        self.store.update_scenario(run.scenario_id, round=1)
        self._say(run, "SYSTEM", "SYSTEM", "Round 1 opens: department proposals.")
        for d in DEPARTMENTS:
            self._check_deadline(run)
            self._apply_department(run, d, run.depts[d].take_turn(self._ctx(run, "PROPOSAL")))
        self._after_round(run)

    def _negotiation_round(self, run: _Run) -> None:
        run.round += 1
        run.commander_requests = {}
        run.round_sacrifice_candidates = []
        self.store.update_scenario(run.scenario_id, round=run.round)
        status = f"plan v{run.plan['version']} {run.plan['status']}" if run.plan else "no plan"
        self._say(run, "SYSTEM", "SYSTEM", f"Round {run.round} opens ({status}).")
        self._check_deadline(run)
        self._apply_commander(run, run.commander.take_turn(self._ctx(run, "NEGOTIATION")))
        cands = run.round_sacrifice_candidates
        order = [d for d in DEPARTMENTS if d not in cands] + [d for d in DEPARTMENTS if d in cands]
        for d in order:
            self._check_deadline(run)
            self._apply_department(run, d, run.depts[d].take_turn(self._ctx(run, "NEGOTIATION")))
        self._after_round(run)

    def _after_round(self, run: _Run) -> None:
        self._draft_plan(run)
        if run.validation and run.validation.passed:
            if run.round >= run.min_rounds:
                self._vote(run)
            else:
                self._say(run, COMMANDER, "SYSTEM",
                          f"Plan v{run.plan['version']} passes validation. Protocol requires at least "
                          f"{run.min_rounds} rounds before approval, so the council continues to round "
                          f"{run.round + 1} for final review.")
        else:
            reasons = run.validation.reasons if run.validation else ["no plan"]
            n = sum(1 for c in run.candidates if not c["requires_override"] or run.policy.crisis_override)
            self._say(run, COMMANDER, "CONFLICT",
                      f"Plan v{run.plan['version']} ({_combo_label(run.plan['modes'])}) is blocked: "
                      + " | ".join(reasons) + f". Feasible full-package combinations under current policy: {n}.",
                      payload={"reasons": reasons})

    # ----------------------------------------------------------- turn effects
    def _apply_commander(self, run: _Run, t: CommanderTurn) -> None:
        meta = self._turn_meta(t)
        reqs: dict[str, str] = {}
        for d, code in {**(t.target_combo or {}), **(t.mode_requests or {})}.items():
            p = MODE_PACKAGES.get(code or "")
            if d in DEPARTMENTS and p and p.department == d and code not in run.forbidden:
                reqs[d] = code  # full target, incl. unchanged modes ("keep your current mode")
        for d in t.sacrifice_candidates or []:
            if d in DEPARTMENTS and d not in reqs:
                reqs[d] = mode_code(d, SACRIFICE)
        cands = [d for d in DEPARTMENTS if d in reqs and MODE_PACKAGES[reqs[d]].tier == SACRIFICE]
        run.commander_requests = reqs
        run.round_sacrifice_candidates = cands
        for d in cands:
            run.sacrifice_requests.setdefault(d, run.round)
        target = t.target_combo or {}
        target_label = _combo_label({d: target.get(d) for d in DEPARTMENTS}) if target else None
        self._say(run, COMMANDER, "REQUEST", t.message or "(no message)", payload={
            "target_combo": target, "target_label": target_label, "mode_requests": reqs,
            "sacrifice_candidates": cands, "use_crisis_override": t.use_crisis_override,
            "declare_infeasible": t.declare_infeasible}, **meta)

        wants_override = t.use_crisis_override or any(
            c.get("requires_override") and c["label"] == target_label for c in run.candidates)
        if wants_override and not run.policy.crisis_override:
            if run.kind == EVENT:
                run.policy = Policy.override()
                self.store.update_scenario(run.scenario_id, policy=run.policy.to_dict())
                self._refresh_candidates(run)
                self._say(run, COMMANDER, "SYSTEM", "Crisis Override invoked: risk limit 24 -> 28 and up to 2 "
                          "Sacrifice modes. Each sacrificing department still needs 2 accepted returns.",
                          payload={"policy": run.policy.to_dict()})
            else:
                self._say(run, "VALIDATOR", "SYSTEM", "Crisis Override request rejected: it is only available "
                          "after an emergency event (guide p8).")
        for offer in t.offers or []:
            self._offer(run, COMMANDER, offer, meta)
        if t.declare_infeasible:
            if not run.candidates:
                diag = optimizer.diagnose_infeasibility(run.pool, run.policy, run.forbidden,
                                                        override_allowed=run.kind == EVENT)
                raise _End("INFEASIBLE", self._infeasible_text(diag), {"diagnosis": diag})
            self._say(run, "VALIDATOR", "SYSTEM",
                      f"Commander's infeasibility claim rejected: the validator search found {len(run.candidates)} "
                      f"feasible combination(s), e.g. {run.candidates[0]['label']}.")

    def _apply_department(self, run: _Run, d: str, t: DepartmentTurn) -> None:
        meta = self._turn_meta(t)
        prev = run.declared.get(d)
        code, note = t.mode, ""
        p = MODE_PACKAGES.get(code or "")
        if not p or p.department != d or code in run.forbidden:
            fallback = prev or mode_code(d, STANDARD)
            note = f" [system: invalid mode {code!r} ignored; keeping {fallback}]"
            code = fallback
        run.declared[d] = code
        if prev and code != prev and run.round > 1:
            run.protocol["revised_or_rejected"] = True
        refused = bool(t.refuses_sacrifice) or t.msg_type == REFUSE_SACRIFICE
        if refused:
            run.protocol["sacrifice_refused"] = True
        pkg = MODE_PACKAGES[code]
        self._say(run, d, t.msg_type, (t.message or "(no message)") + note, payload={
            "mode": code, "previous_mode": prev, "tier": pkg.tier, "package": pkg.resources(), "risk": pkg.risk,
            "reason": t.reason, "consequence": t.consequence, "refuses_sacrifice": refused,
            "proposed_combo": t.proposed_combo}, **meta)
        for offer in t.offers or []:
            self._offer(run, d, offer, meta)
        by_id = {c.id: c for c in self.store.commitments(run.scenario_id)}
        for cid, new_status, mtype in [(i, "ACCEPTED", "COMMITMENT_ACCEPTED") for i in t.accept_commitment_ids or []] + \
                                       [(i, "REJECTED", "COMMITMENT_REJECTED") for i in t.reject_commitment_ids or []]:
            c = by_id.get(cid)
            if not c or c.beneficiary != d or c.status != "PROPOSED":
                continue
            self.store.update_commitment(cid, status=new_status, round_resolved=run.round)
            c.status = new_status
            verb = "accepts" if new_status == "ACCEPTED" else "rejects"
            self._say(run, d, mtype, f"{_label(d)} {verb} {cid} from {_label(c.owner)}: {c.promise}",
                      payload=c.to_dict(), **meta)

    def _offer(self, run: _Run, owner: str, o: CommitmentOffer, meta: dict[str, Any]) -> None:
        if o.template_id:
            tpl = _TEMPLATES_BY_ID.get(o.template_id)
            if tpl is None or tpl.owner != owner:
                self._say(run, "VALIDATOR", "SYSTEM", f"Ignored commitment offer from {_label(owner)}: template "
                          f"{o.template_id!r} does not exist or belongs to another agent.")
                return
            fields = dict(beneficiary=tpl.beneficiary, kind=tpl.kind, resource=tpl.resource, amount=tpl.amount,
                          source=tpl.source, claim=tpl.claim, promise=tpl.promise, expiry=tpl.expiry,
                          template_id=tpl.template_id)
        else:
            problems = []
            if o.beneficiary not in DEPARTMENTS or o.beneficiary == owner:
                problems.append("beneficiary must be another department")
            if o.kind not in ("resource", "priority"):
                problems.append("kind must be resource or priority")
            if o.kind == "resource" and (o.resource not in RESOURCES or int(o.amount or 0) <= 0):
                problems.append("resource commitments need a valid resource and a positive amount")
            if not (o.promise or "").strip():
                problems.append("promise text is empty")
            if problems:
                self._say(run, "VALIDATOR", "SYSTEM",
                          f"Ignored custom commitment offer from {_label(owner)}: {'; '.join(problems)}.")
                return
            fields = dict(beneficiary=o.beneficiary, kind=o.kind, resource=o.resource, amount=int(o.amount or 0),
                          source=o.source, claim=o.claim, promise=o.promise.strip(),
                          expiry=o.expiry or "End of next planning cycle", template_id=None)
        for c in self.store.commitments(run.scenario_id):
            if c.owner == owner and c.beneficiary == fields["beneficiary"] and c.status in ("PROPOSED", "ACCEPTED") \
                    and ((fields["template_id"] and c.template_id == fields["template_id"]) or c.promise == fields["promise"]):
                return  # already on the table
        c = Commitment(id=self.store.next_commitment_id(run.scenario_id), scenario_id=run.scenario_id, owner=owner,
                       status="PROPOSED", round_offered=run.round, **fields)
        self.store.add_commitment(run.session_id, c)
        self._say(run, owner, "COMMITMENT", f"{_label(owner)} offers {c.id} to {_label(c.beneficiary)}: "
                  f"{c.promise} (expires: {c.expiry})", payload=c.to_dict(), recipient=c.beneficiary, **meta)

    # ------------------------------------------------------- plans and votes
    def _draft_plan(self, run: _Run) -> None:
        modes = {d: run.declared.get(d) for d in DEPARTMENTS}
        comms = self.store.commitments(run.scenario_id)
        sacrificing = {d for d, c in modes.items() if c and MODE_PACKAGES[c].tier == SACRIFICE}
        relevant = sorted(c.id for c in comms if c.status == "ACCEPTED" and c.beneficiary in sacrificing)
        prev = run.plan
        if prev and prev["modes"] == modes and prev["commitment_ids"] == relevant \
                and prev["policy"] == run.policy.to_dict():
            return  # nothing changed: same version stays current
        valid_modes = {d: c for d, c in modes.items() if c}
        v = validator.validate_plan(valid_modes, comms, run.pool, run.policy, run.forbidden)
        if prev:
            cleared = self.store.invalidate_votes(prev["id"])
            if prev["status"] in ("VALIDATED", "VOTING", "DRAFT"):
                self.store.update_plan(prev["id"], status="SUPERSEDED")
            if prev["modes"] != modes:
                run.protocol["revised_or_rejected"] = True
        version = self.store.next_plan_version(run.session_id)
        plan = self.store.add_plan(
            session_id=run.session_id, scenario_id=run.scenario_id, version=version, modes=modes,
            commitment_ids=relevant, totals=v.totals, reserve=v.reserve, risk=v.risk, policy=run.policy.to_dict(),
            status="VALIDATED" if v.passed else "FAILED", validation=v.to_dict(), round=run.round,
        )
        run.plan, run.validation = plan, v
        run.protocol["return_commitments"] = len(relevant)
        self.store.update_scenario(run.scenario_id, protocol=run.protocol)
        if prev and cleared:
            self._say(run, "SYSTEM", "VOTES_CLEARED", f"Plan changed v{prev['version']} -> v{version}: "
                      f"{cleared} earlier vote(s) cleared. A fresh vote is required on v{version}.")
        if not v.passed:
            run.protocol["revised_or_rejected"] = True
        diff = ""
        if prev:
            changed = [f"{_label(d)} {prev['modes'].get(d)}->{modes[d]}" for d in DEPARTMENTS
                       if prev["modes"].get(d) != modes[d]]
            diff = f" Changes vs v{prev['version']}: {', '.join(changed) or 'commitments/policy only'}."
        self._say(run, COMMANDER, "PLAN_DRAFT",
                  f"Plan v{version} drafted from declared modes: {_combo_label(modes)} | used {_fmt_res(v.totals)} | "
                  f"reserve {_fmt_res(v.reserve)} | risk {v.risk}/{run.policy.risk_limit} | "
                  f"returns: {', '.join(relevant) or 'none'}.{diff}",
                  payload={"plan_id": plan["id"], "version": version, "modes": modes, "totals": v.totals,
                           "reserve": v.reserve, "risk": v.risk, "commitment_ids": relevant})
        checks = "; ".join(f"{'PASS' if c.passed else 'FAIL'} {c.name}: {c.detail}" for c in v.checks)
        self._say(run, "VALIDATOR", "VALIDATION", f"{v.status} for plan v{version}. {checks}",
                  payload=v.to_dict(), source="validator")

    def _vote(self, run: _Run) -> None:
        plan = run.plan
        self.store.invalidate_votes(plan["id"])  # a re-vote on an unchanged version starts clean
        self.store.update_plan(plan["id"], status="VOTING")
        plan["status"] = "VOTING"
        self._say(run, COMMANDER, "VOTE_CALL", f"Plan v{plan['version']} ({_combo_label(plan['modes'])}) passed "
                  f"validation. All four departments: vote ACCEPT or REJECT on v{plan['version']}.")
        votes: list[Vote] = []
        for d in DEPARTMENTS:
            self._check_deadline(run)
            vote = self._record_vote(run, plan, d, run.depts[d].vote(self._ctx(run, "VOTE")))
            if vote.decision == REJECT and run.validation.passed and hasattr(run.depts[d], "reconsider_vote"):
                snap = factcheck.plan_snapshot(plan, run.validation, self.store.commitments(run.scenario_id),
                                               run.pool, run.declared)
                corrections = factcheck.find_contradictions(d, vote.reason, snap)
                if corrections:
                    self._say(run, "VALIDATOR", "FACT_CHECK",
                              f"Fact check for {_label(d)}'s REJECT of v{plan['version']}: " + " ".join(corrections)
                              + " The original vote is kept in the record; it may reconsider once.",
                              payload={"original_vote": vote.to_dict(), "corrections": corrections},
                              source="validator", recipient=d)
                    self._check_deadline(run)
                    self.store.invalidate_vote(plan["id"], d)  # superseded by the reconsidered vote below
                    vote = self._record_vote(run, plan, d,
                                             run.depts[d].reconsider_vote(self._ctx(run, "VOTE"), vote, corrections))
            votes.append(vote)
        current_version = self.store.next_plan_version(run.session_id) - 1
        ok, reasons = validator.approval_gate(run.validation, votes, plan["version"], current_version,
                                              run.round, run.min_rounds)
        if ok:
            run.protocol["rounds_before_approval"] = run.round
            run.protocol["return_commitments"] = len(plan["commitment_ids"])
            if run.cosign and run.validation.risk > COSIGN_RISK_THRESHOLD:
                self.store.update_plan(plan["id"], status="AWAITING_COSIGN")
                plan["status"] = "AWAITING_COSIGN"
                self._say(run, COMMANDER, "SYSTEM",
                          f"Plan v{plan['version']} cleared the approval gate (validator PASS, 4/4 ACCEPT on "
                          f"v{plan['version']}). Human-in-the-loop: risk {run.validation.risk} > "
                          f"{COSIGN_RISK_THRESHOLD}, so the judge must co-sign before it takes effect.",
                          payload={"plan_id": plan["id"], "version": plan["version"]})
                raise _End("AWAITING_COSIGN", f"Plan v{plan['version']} passed the gate in round {run.round} and "
                           f"awaits the judge co-sign (risk {run.validation.risk} > {COSIGN_RISK_THRESHOLD}).")
            self.store.update_plan(plan["id"], status="APPROVED")
            plan["status"] = "APPROVED"
            self._say(run, COMMANDER, "APPROVAL", self._approval_text(plan, run.validation.to_dict()),
                      payload={"plan_id": plan["id"], "version": plan["version"],
                               "validation": run.validation.to_dict()})
            raise _End("APPROVED", f"Plan v{plan['version']} approved in round {run.round}.")
        self.store.update_plan(plan["id"], status="REJECTED")
        plan["status"] = "REJECTED"
        run.protocol["revised_or_rejected"] = True
        self._say(run, "VALIDATOR", "APPROVAL_BLOCKED",
                  f"Approval gate blocked plan v{plan['version']}: " + " | ".join(reasons),
                  payload={"reasons": reasons}, source="validator")

    def _record_vote(self, run: _Run, plan: dict[str, Any], d: str, vote: Vote) -> Vote:
        vote.agent = d
        if vote.decision not in (ACCEPT, REJECT):
            vote.reason = f"{vote.reason} (invalid decision {vote.decision!r} recorded as REJECT)"
            vote.decision = REJECT
        self.store.add_vote(plan["id"], run.scenario_id, vote)
        tag = " (reconsidered after fact check)" if vote.reconsidered else ""
        self._say(run, d, "VOTE", f"{vote.decision} plan v{vote.plan_version}{tag}: {vote.reason}",
                  payload=vote.to_dict(), source=vote.source, fallback_reason=vote.fallback_reason,
                  provider=vote.provider, model=vote.model)
        return vote

    # --------------------------------------------------------------- events
    def _prepare_after_event(self, run: _Run, prev: dict[str, Any]) -> None:
        stale = self.store.latest_approved_plan(run.session_id, include_stale=False)
        if stale is None:
            last = self.store.latest_plan(prev["id"])
            if last:
                run.declared = dict(last["modes"])
            self._say(run, "SYSTEM", "SYSTEM", "No approved plan existed before this event; renegotiating "
                      "from the last declared modes.", round_no=0)
            return
        old_comms = [c for c in self.store.commitments(stale["scenario_id"]) if c.id in stale["commitment_ids"]]
        v = validator.validate_plan(stale["modes"], old_comms, run.pool, Policy.baseline(), run.forbidden)
        if v.passed:
            why = (f"The event changed the colony state; plan v{stale['version']} still fits the new pool but must "
                   "be renegotiated and re-voted.")
        else:
            why = f"The event breaks plan v{stale['version']}: " + " | ".join(v.reasons)
        self.store.update_plan(stale["id"], status="STALE", note=why)
        self.store.invalidate_votes(stale["id"])  # a STALE plan's votes no longer count
        self._say(run, "VALIDATOR", "PLAN_STALE", f"Plan v{stale['version']} ({_combo_label(stale['modes'])}) is "
                  f"now STALE. {why} Its votes no longer count.", payload=v.to_dict(), source="validator",
                  round_no=0)
        old_totals = stale.get("totals") or {}
        for c in old_comms:
            problems = []
            if c.source == "reserve" and c.resource:
                reserve_now = run.pool.get(c.resource, 0) - old_totals.get(c.resource, 0)
                if reserve_now < c.amount:
                    problems.append(f"{RESOURCE_LABELS[c.resource]} reserve is now {reserve_now}, "
                                    f"below the promised {c.amount}")
            owner_mode = stale["modes"].get(c.owner)
            if owner_mode and owner_mode in run.forbidden:
                problems.append(f"owner's mode {owner_mode} is no longer available")
            if problems:
                self.store.update_commitment(c.id, status="VOIDED", note="; ".join(problems))
                self._say(run, "VALIDATOR", "COMMITMENT_VOIDED", f"{c.id} ({_label(c.owner)} -> "
                          f"{_label(c.beneficiary)}: {c.promise}) VOIDED — {'; '.join(problems)}.",
                          payload=c.to_dict(), source="validator", round_no=0)
                continue
            new = Commitment(id=self.store.next_commitment_id(run.scenario_id), scenario_id=run.scenario_id,
                             owner=c.owner, beneficiary=c.beneficiary, kind=c.kind, resource=c.resource,
                             amount=c.amount, source=c.source, claim=c.claim, promise=c.promise, expiry=c.expiry,
                             status="PROPOSED", template_id=c.template_id, round_offered=0,
                             note=f"Carried over from {c.id}; beneficiary must re-accept.")
            self.store.add_commitment(run.session_id, new)
            self._say(run, "SYSTEM", "COMMITMENT", f"{new.id} carried over from {c.id} for renegotiation "
                      f"({_label(c.owner)} -> {_label(c.beneficiary)}: {c.promise}); it must be re-accepted.",
                      payload=new.to_dict(), round_no=0)
        run.declared = dict(stale["modes"])
        for d, code in stale["modes"].items():
            if code in MODE_PACKAGES and MODE_PACKAGES[code].tier == SACRIFICE:
                run.sacrifice_requests[d] = 0  # accepted in the approved plan (round 0 = before this scenario)

    def _approval_text(self, plan: dict[str, Any], validation: dict[str, Any], cosigned: bool = False) -> str:
        comms = {c.id: c for c in self.store.commitments(plan["scenario_id"])}
        returns = "; ".join(f"{cid} {_label(comms[cid].owner)}->{_label(comms[cid].beneficiary)}: "
                            f"{comms[cid].promise.rstrip('.')}" for cid in plan["commitment_ids"] if cid in comms)
        n = len(validation.get("checks") or [])
        return (f"APPROVED plan v{plan['version']} ({_combo_label(plan['modes'])}). Validator PASS ({n}/{n} checks); "
                f"4/4 ACCEPT votes on v{plan['version']}{'; co-signed by the judge' if cosigned else ''}. "
                f"Used {_fmt_res(validation.get('totals') or {})}; reserve {_fmt_res(validation.get('reserve') or {})}; "
                f"risk {validation.get('risk')}/{(plan.get('policy') or {}).get('risk_limit')}. "
                f"Return agreements: {returns or 'none required'}.")

    def cosign(self, approve: bool, note: str = "") -> dict[str, Any]:
        """Human-in-the-loop decision on a plan that already passed the deterministic gate."""
        session = self.store.current_session()
        scenario = self.store.latest_scenario(session["id"]) if session else None
        if not scenario or scenario["status"] != "AWAITING_COSIGN":
            raise ValueError("No plan is waiting for a co-sign.")
        plan = self.store.latest_plan(scenario["id"])
        base = {"session_id": scenario["session_id"], "scenario_id": scenario["id"], "round": scenario["round"],
                "plan_version": plan["version"], "recipient": "ALL", "source": "system", "payload": {"note": note}}
        if approve:
            self.store.update_plan(plan["id"], status="APPROVED")
            self.store.add_message({**base, "sender": "JUDGE", "msg_type": "SYSTEM",
                                    "content": f"Judge co-signed plan v{plan['version']}. {note}".strip()})
            self.store.add_message({**base, "sender": COMMANDER, "msg_type": "APPROVAL",
                                    "content": self._approval_text(plan, plan.get("validation") or {}, True)})
            reason = f"Plan v{plan['version']} approved in round {scenario['round']} and co-signed by the judge."
            self.store.update_scenario(scenario["id"], status="APPROVED", result_reason=reason, finished_at=now())
        else:
            self.store.update_plan(plan["id"], status="REJECTED", note=f"Vetoed by judge. {note}".strip())
            self.store.add_message({**base, "sender": "JUDGE", "msg_type": "SYSTEM",
                                    "content": f"Judge vetoed plan v{plan['version']}; it does not take effect. "
                                               f"{note}".strip()})
            protocol = dict(scenario.get("protocol") or {})
            protocol["rounds_before_approval"] = 0
            self.store.update_scenario(scenario["id"], status="VETOED", protocol=protocol, finished_at=now(),
                                       result_reason=f"Judge vetoed plan v{plan['version']}. Start a new run.")
        return self.store.get_scenario(scenario["id"])

    # -------------------------------------------------------------- endings
    @staticmethod
    def _infeasible_text(diag: dict[str, Any]) -> str:
        blocking = "; ".join(diag.get("blocking") or []) or "no combination satisfies the limits"
        sugg = "; ".join(diag.get("suggestions") or []) or "additional resources are required"
        nearest = diag.get("nearest") or {}
        near = ""
        if nearest.get("label"):
            short = ", ".join(f"{RESOURCE_LABELS[r]} -{v}" for r, v in (nearest.get("shortfall") or {}).items() if v)
            near = f" Nearest plan {nearest['label']} is short {short or 'nothing on resources'}."
        return f"INFEASIBLE — blocking constraints: {blocking}.{near} Requested change: {sugg}."

    def _deadlock(self, run: _Run) -> None:
        if not run.candidates:
            diag = optimizer.diagnose_infeasibility(run.pool, run.policy, run.forbidden,
                                                    override_allowed=run.kind == EVENT)
            raise _End("INFEASIBLE", self._infeasible_text(diag), {"diagnosis": diag})
        last = f"Last plan v{run.plan['version']} ({run.plan['status']})" if run.plan else "No plan"
        reasons = " | ".join(run.validation.reasons) if run.validation and run.validation.reasons else \
            "votes did not reach 4 ACCEPT"
        raise _End("DEADLOCK", f"DEADLOCK after {run.round} rounds: no plan reached validator PASS plus 4 ACCEPT "
                   f"votes. {last}: {reasons}. Feasible combinations still exist (e.g. {run.candidates[0]['label']}); "
                   "no new plan was approved.")

    def _finish(self, run: _Run, end: _End) -> None:
        run.protocol["rounds"] = run.round
        if end.status not in ("APPROVED", "AWAITING_COSIGN"):
            run.protocol["rounds_before_approval"] = 0
            stale = self.store.latest_approved_plan(run.session_id)
            tail = f" Previous approved plan v{stale['version']} remains {stale['status']}." if stale else ""
            msg_type = end.status if end.status in ("DEADLOCK", "INFEASIBLE", "TIMEOUT") else "SYSTEM"
            self._say(run, COMMANDER, msg_type, end.reason + tail, payload=end.extra)
        self.store.update_scenario(run.scenario_id, status=end.status, result_reason=end.reason, finished_at=now(),
                                   round=run.round, protocol=run.protocol, policy=run.policy.to_dict(),
                                   result=end.extra or None)
