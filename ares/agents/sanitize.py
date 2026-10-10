"""Sanitise raw LLM JSON into contract objects. Model output is data only (no eval/exec).

Hard protocol rules are enforced here (own codes only, no same-round Sacrifice, Sacrifice only with
>= 2 offers from different agents, own templates only, ids must exist and target this agent);
everything else (tone, objections, counter-proposals, votes) is left to the model.
"""
from __future__ import annotations

from typing import Any

from ares.models import (ACCEPT, ACCEPT_MODE, ACCEPT_SACRIFICE, COMMANDER, COUNTEROFFER, DEFAULT_EXPIRY, DEPARTMENTS,
                         DEPT_MSG_TYPES, MODE_PACKAGES, OBJECTION, PROPOSAL, REFUSE_SACRIFICE, REJECT, RESOURCES,
                         RESTRICTED, SACRIFICE, STANDARD, AgentContext, CommanderTurn, CommitmentOffer,
                         DepartmentTurn, Vote)
from ares.agents import analysis as A
from ares.agents import rule_policy

MSG_CAP = 600
TEXT_CAP = 400


def _s(v: Any, cap: int) -> str:
    return A.short(v if isinstance(v, (str, int, float)) else "", cap)


def _list(v: Any) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, (str, int)) and str(v).strip():
        return [str(v)]
    return []


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


def department_raw_ok(raw: Any) -> bool:
    return isinstance(raw, dict) and any(k in raw for k in ("mode", "msg_type", "message"))


def vote_raw_ok(raw: Any) -> bool:
    return isinstance(raw, dict) and any(k in raw for k in ("vote", "decision"))


def commander_raw_ok(raw: Any) -> bool:
    return isinstance(raw, dict) and any(k in raw for k in ("target_label", "mode_requests", "declare_infeasible",
                                                            "target_combo"))


def _custom_offer(d: Any, me: str) -> CommitmentOffer | None:
    if not isinstance(d, dict):
        return None
    ben = str(d.get("beneficiary") or "").upper()
    if ben not in DEPARTMENTS or ben == me:
        return None
    kind = d.get("kind") if d.get("kind") in ("resource", "priority") else "priority"
    res = d.get("resource") if d.get("resource") in RESOURCES else None
    try:
        amount = max(0, min(20, int(d.get("amount") or 0)))
    except (TypeError, ValueError):
        amount = 0
    allowed_src = ("package", "future", "reserve") if me == COMMANDER else ("package", "future")
    src = d.get("source") if d.get("source") in allowed_src else None
    if kind == "resource" and (res is None or amount <= 0 or src is None):
        return None
    if kind == "priority":
        res, amount, src = None, 0, None
    promise = _s(d.get("promise"), 200)
    if not promise:
        return None
    claim = _s(d.get("claim"), 60) or None
    return CommitmentOffer(beneficiary=ben, kind=kind, resource=res, amount=amount, source=src, claim=claim,
                           promise=promise, expiry=_s(d.get("expiry"), 80) or DEFAULT_EXPIRY)


def _template_offers(ids: Any, me: str, ctx: AgentContext, notes: list[str]) -> list[CommitmentOffer]:
    out, seen = [], set()
    for tid in _list(ids):
        t = A.template_by_id(str(tid).strip().upper())
        if t is None or t.owner != me:
            notes.append(f"dropped template {tid!s:.12} (not owned)")
            continue
        if t.template_id in seen or A.already_offered(ctx, me, t.template_id):
            continue
        seen.add(t.template_id)
        out.append(CommitmentOffer(beneficiary=t.beneficiary, template_id=t.template_id))
    return out


def department_turn(raw: dict, agent: Any, ctx: AgentContext) -> DepartmentTurn:
    me = agent.agent_id
    notes: list[str] = []
    codes = A.codes_for(me)
    sac_code = codes[SACRIFICE]
    prev = (ctx.declared_modes or {}).get(me) or agent.state.get("last_mode")
    prev = prev if A.owns_code(me, prev) else None
    is_proposal = ctx.phase == "PROPOSAL"

    mode = str(raw.get("mode") or "").strip().upper()
    if not A.owns_code(me, mode) or not A.allowed(ctx, mode):
        fallback = (A.pick_allowed(ctx, me, [STANDARD, RESTRICTED]) if is_proposal or not prev else prev)
        notes.append(f"invalid mode {mode[:6]!r} replaced by {fallback}")
        mode = fallback
    msg_type = str(raw.get("msg_type") or "").strip().upper()
    refuses = _bool(raw.get("refuses_sacrifice"))
    req = (ctx.commander_requests or {}).get(me)
    sac_asked = A.tier_of(req) == SACRIFICE or A.first_sacrifice_round(ctx, me) is not None

    if is_proposal:
        msg_type = PROPOSAL
    elif msg_type not in DEPT_MSG_TYPES or msg_type == PROPOSAL:
        if refuses:
            msg_type = REFUSE_SACRIFICE
        elif A.tier_of(mode) == SACRIFICE:
            msg_type = ACCEPT_SACRIFICE
        elif req and mode == req:
            msg_type = ACCEPT_MODE
        elif req:
            msg_type = COUNTEROFFER
        else:
            msg_type = ACCEPT_MODE

    # --- consistency between mode, refusal flag and msg_type
    if msg_type == ACCEPT_SACRIFICE and A.tier_of(mode) != SACRIFICE:
        mode = sac_code
    if A.tier_of(mode) == SACRIFICE and (refuses or msg_type == REFUSE_SACRIFICE):
        refuses, msg_type = True, REFUSE_SACRIFICE
        mode = prev if prev and A.tier_of(prev) != SACRIFICE else A.pick_allowed(ctx, me, [RESTRICTED, STANDARD])
        notes.append("refusal kept; Sacrifice mode withdrawn")
    if msg_type == REFUSE_SACRIFICE or refuses:
        if sac_asked:
            refuses, msg_type = True, REFUSE_SACRIFICE
        else:
            refuses = False
            msg_type = OBJECTION
            notes.append("no Sacrifice was requested; refusal recorded as objection")

    offers_to_me = A.distinct_owner_offers(ctx, me)
    accept_ids = [i for i in (str(x) for x in _list(raw.get("accept_commitment_ids")))
                  if any(c.id == i and c.beneficiary == me and c.status == "PROPOSED" for c in ctx.commitments or [])]
    reject_ids = [i for i in (str(x) for x in _list(raw.get("reject_commitment_ids")))
                  if i not in accept_ids and
                  any(c.id == i and c.beneficiary == me and c.status == "PROPOSED" for c in ctx.commitments or [])]

    raw_mode_was_sacrifice = A.tier_of(mode) == SACRIFICE
    # --- hard rule: Sacrifice never in its first round; only with >= 2 offers from different agents
    if A.tier_of(mode) == SACRIFICE and not (prev == sac_code and len(A.accepted_owners(ctx, me)) >= 2):
        if not A.may_accept_sacrifice_now(ctx, me):
            why = "Sacrifice cannot be accepted in the round it is first requested"
        elif len(offers_to_me) < 2:
            why = f"only {len(offers_to_me)} return commitment(s) from different agents on the table"
        else:
            why = ""
        if why:
            notes.append(why)
            mode = A.pick_allowed(ctx, me, [RESTRICTED, STANDARD])
            if sac_asked:
                msg_type, refuses = REFUSE_SACRIFICE, True
            else:
                msg_type = OBJECTION if msg_type == ACCEPT_SACRIFICE else msg_type
            accept_ids = []
        else:
            msg_type = ACCEPT_SACRIFICE if prev != sac_code else msg_type
            covered = {c.owner for c in offers_to_me if c.id in accept_ids or c.status == "ACCEPTED"}
            if len(covered) < 2:
                extra = [c.id for c in offers_to_me if c.status == "PROPOSED" and c.id not in accept_ids]
                accept_ids += extra
                if extra:
                    notes.append(f"accepting offers {', '.join(extra)} that back the Sacrifice")
            reject_ids = [i for i in reject_ids if i not in accept_ids]

    # --- counter-proposal
    combo = None
    lab = raw.get("counter_combo_label") or raw.get("proposed_combo")
    if isinstance(lab, dict):
        lab = A.combo_label(lab)
    if lab:
        cand = A.candidate_by_label(ctx, lab)
        combo = dict(cand["modes"]) if cand else A.parse_combo(lab)
        if combo is None:
            notes.append("unparseable counter-proposal dropped")

    offers = _template_offers(raw.get("offer_template_ids"), me, ctx, notes)
    for d in _list(raw.get("custom_offers"))[:2]:
        o = _custom_offer(d, me)
        if o is not None:
            offers.append(o)

    message = _s(raw.get("message"), MSG_CAP) or f"{agent.profile['name']} selects {mode}."
    if raw_mode_was_sacrifice and A.tier_of(mode) != SACRIFICE:
        # the model tried to accept Sacrifice but a council rule blocked it: say so in the visible text
        message = A.short(f"[Council rule overrode this agent's Sacrifice acceptance: {notes[-1] if notes else ''}; "
                          f"recorded as {msg_type} at {mode}.] {message}", MSG_CAP)
    if combo and A.combo_label(combo) not in message:
        message = A.short(f"{message} Counter-proposal: {A.combo_label(combo)}.", MSG_CAP)
    reason = _s(raw.get("reason"), TEXT_CAP)
    if notes:
        reason = A.short(f"{reason} [sanitised: {'; '.join(notes)}]".strip(), TEXT_CAP)
    consequence = _s(raw.get("consequence"), TEXT_CAP) or (
        rule_policy.proposal_consequence(me) if is_proposal else MODE_PACKAGES[mode].consequence)
    agent._last_sanitize_notes = notes
    return DepartmentTurn(agent=me, msg_type=msg_type, mode=mode, message=message, reason=reason,
                          consequence=consequence, offers=offers, accept_commitment_ids=accept_ids,
                          reject_commitment_ids=reject_ids, refuses_sacrifice=refuses, proposed_combo=combo)


def vote(raw: dict, agent: Any, ctx: AgentContext) -> Vote:
    d = str(raw.get("vote") or raw.get("decision") or "").strip().upper()
    decision = ACCEPT if d in ("ACCEPT", "APPROVE", "YES", "ACCEPTED") else REJECT
    reason = _s(raw.get("reason"), 300) or ("Accepts the plan." if decision == ACCEPT else "Rejects the plan.")
    return Vote(agent=agent.agent_id, plan_version=A.plan_version(ctx), decision=decision, reason=reason)


def commander_turn(raw: dict, agent: Any, ctx: AgentContext, sc: dict) -> CommanderTurn:
    notes: list[str] = []
    cands = [c for c in (ctx.candidates or []) if ctx.override_available or not c.get("requires_override")]
    message = _s(raw.get("message"), MSG_CAP)
    if not cands:
        inf = ctx.infeasibility or {}
        if not message or not _bool(raw.get("declare_infeasible")):
            fallback = rule_policy.commander_turn(agent, ctx)
            message = message or fallback.message
        if inf.get("suggestions") and not any(str(s)[:20] in message for s in inf["suggestions"]):
            message = A.short(f"{message} Requested change: {'; '.join(map(str, inf['suggestions'][:2]))}.", MSG_CAP)
        return CommanderTurn(message=message, declare_infeasible=True)
    target = A.candidate_by_label(ctx, raw.get("target_label"))
    if target is None and isinstance(raw.get("target_combo"), dict):
        target = A.candidate_for_modes(ctx, raw["target_combo"])
    if target is None and isinstance(raw.get("mode_requests"), dict):
        target = A.candidate_for_modes(ctx, {str(k).upper(): str(v).upper() for k, v in raw["mode_requests"].items()})
    if target is not None and target.get("requires_override") and not ctx.override_available:
        target = None
    if target is None:
        target = rule_policy.choose_target(ctx, sc)
        notes.append(f"target {str(raw.get('target_label'))[:20]!r} not a feasible candidate; using {A.cand_label(target)}")
    sacs = A.cand_sacrifices(target)
    offers: list[CommitmentOffer] = []
    seen = set()
    for tid in _list(raw.get("offer_template_ids")):
        t = A.template_by_id(str(tid).strip().upper())
        if t is None or t.owner != COMMANDER or t.beneficiary not in sacs or t.template_id in seen:
            continue
        if A.already_offered(ctx, COMMANDER, t.template_id) or A.claim_conflict(ctx, t.claim, t.beneficiary):
            continue
        seen.add(t.template_id)
        offers.append(CommitmentOffer(beneficiary=t.beneficiary, template_id=t.template_id))
    # protocol: the Commander offers its own templates to the sacrifice candidates
    covered = {o.beneficiary for o in offers}
    auto, auto_txt = rule_policy.commander_offers(ctx, [s for s in sacs if s not in covered])
    if auto:
        offers += auto
        notes.append("offered Commander return template(s) per protocol")
    if not message:
        message = f"Target {A.cand_label(target)}."
    if notes:
        message = A.short(f"{message} [sanitised: {'; '.join(notes)}]", MSG_CAP)
    sc["target"] = A.cand_label(target)
    sc.setdefault("history", []).append([ctx.round_no, A.cand_label(target)])
    return CommanderTurn(message=message, target_combo=dict(target["modes"]), mode_requests=dict(target["modes"]),
                         sacrifice_candidates=list(sacs), offers=offers,
                         use_crisis_override=bool(ctx.override_available and target.get("requires_override")))
