"""Deterministic rule-based policy (labelled fallback; NOT an LLM).

Every decision is computed at runtime from the AgentContext (pool, validation, candidates,
commitments, requests, transcript) plus the agent's own private state. Nothing here is a script:
there are no fixed transcripts, winning plans or decision sequences.
"""
from __future__ import annotations

from typing import Any

from ares.models import (ACCEPT, ACCEPT_MODE, ACCEPT_SACRIFICE, COMMANDER, DEPARTMENTS, MODE_PACKAGES, OBJECTION,
                         PROPOSAL, REFUSE_SACRIFICE, REJECT, RESTRICTED, SACRIFICE, STANDARD, AgentContext,
                         CommanderTurn, CommitmentOffer, DepartmentTurn, Vote)
from ares.agents import analysis as A

RULE = "rule-based"


# ---------------------------------------------------------------------------
# Department
# ---------------------------------------------------------------------------
def template_offers(me: str, ctx: AgentContext) -> tuple[list[CommitmentOffer], list[str]]:
    """Offer every return template I own for a department currently asked to sacrifice."""
    offers, texts = [], []
    for target in A.sacrifice_targets(ctx):
        if target == me:
            continue
        for tpl in A.templates_owned(me, target):
            if A.already_offered(ctx, me, tpl.template_id):
                continue
            offers.append(CommitmentOffer(beneficiary=target, template_id=tpl.template_id))
            texts.append(f"{tpl.template_id}: {tpl.promise}")
    return offers, texts


def _conflict_text(ctx: AgentContext) -> str:
    reasons = A.validation_reasons(ctx)
    if reasons:
        return "; ".join(A.short(r, 110) for r in reasons[:2])
    over = A.over_pool(A.plan_modes(ctx) or dict(ctx.declared_modes or {}), ctx.pool)
    return ("over pool: " + ", ".join(over)) if over else "plan not yet validated"


def proposal_consequence(me: str) -> str:
    codes = A.codes_for(me)
    r, s = MODE_PACKAGES[codes[RESTRICTED]], MODE_PACKAGES[codes[SACRIFICE]]
    return (f"Restricted {r.code} (risk {r.risk}): {r.consequence} "
            f"Sacrifice {s.code} (risk {s.risk}): {s.consequence}")


def department_turn(agent: Any, ctx: AgentContext) -> DepartmentTurn:
    me, prof, st = agent.agent_id, agent.profile, agent.state
    name = prof["name"]
    codes = A.codes_for(me)
    sac_code = codes[SACRIFICE]
    prev = (ctx.declared_modes or {}).get(me) or st.get("last_mode")
    if not A.owns_code(me, prev):
        prev = None
    offers, offer_texts = template_offers(me, ctx)
    offer_note = (" I offer " + "; ".join(offer_texts) + ".") if offer_texts else ""

    def make(msg_type: str, mode: str, message: str, reason: str, consequence: str = "", **kw: Any) -> DepartmentTurn:
        if not consequence:
            consequence = MODE_PACKAGES[mode].consequence
        return DepartmentTurn(agent=me, msg_type=msg_type, mode=mode, message=A.short(message + offer_note, 600),
                              reason=A.short(reason, 400), consequence=A.short(consequence, 400), offers=offers,
                              source=RULE, **kw)

    # ---- Round 1: request Standard and explain what Restricted / Sacrifice would cost.
    if ctx.phase == "PROPOSAL" or (prev is None and not ctx.commander_requests):
        mode = A.pick_allowed(ctx, me, [STANDARD, RESTRICTED])
        p = MODE_PACKAGES[mode]
        msg = (f"{name} requests {mode} ({A.fmt_res(p.resources())}, risk {p.risk}) to {prof['role'][0].lower()}"
               f"{prof['role'][1:].rstrip('.')}. Main concern: {prof['main_concern']}. "
               f"{proposal_consequence(me)}")
        return make(PROPOSAL, mode, msg, f"Protects our mission; key resources: {', '.join(prof['key_resources'])}.",
                    proposal_consequence(me))

    req = (ctx.commander_requests or {}).get(me)
    req_tier = A.tier_of(req) if A.owns_code(me, req) else None
    prev_tier = A.tier_of(prev)
    sid = str(ctx.scenario_id)

    # ---- Sacrifice requested (or currently held).
    if req_tier == SACRIFICE or (req is None and prev_tier == SACRIFICE):
        offers_to_me = A.distinct_owner_offers(ctx, me)
        owners = [A.label(c.owner) for c in offers_to_me]
        sac_pkg = MODE_PACKAGES[sac_code]
        if prev_tier == SACRIFICE and len(A.accepted_owners(ctx, me)) >= 2:
            return make(ACCEPT_MODE, sac_code,
                        f"{name} holds {sac_code} under accepted returns from {', '.join(owners)}. "
                        f"Cost remains: {sac_pkg.consequence}", "Return agreement in place.")
        if A.may_accept_sacrifice_now(ctx, me) and len(offers_to_me) >= 2 and A.allowed(ctx, sac_code):
            ids = [c.id for c in offers_to_me if c.status == "PROPOSED"]
            promises = "; ".join(f"{A.label(c.owner)}: {A.short(c.promise, 70)}" for c in offers_to_me[:3])
            st.setdefault("grievances", []).append(f"S{sid} R{ctx.round_no}: accepted {sac_code} - "
                                                   f"{sac_pkg.consequence}")
            return make(ACCEPT_SACRIFICE, sac_code,
                        f"{name} accepts Sacrifice {sac_code} ({A.fmt_res(sac_pkg.resources())}, risk "
                        f"{sac_pkg.risk}) for the colony, on {len(offers_to_me)} return commitments from different "
                        f"agents - {promises.rstrip('.')}. Accepting {', '.join(ids) or 'the accepted returns'}.",
                        "Two return commitments from different agents are on the table.",
                        sac_pkg.consequence, accept_commitment_ids=ids)
        # refuse
        first = A.first_sacrifice_round(ctx, me)
        if not A.may_accept_sacrifice_now(ctx, me):
            why = f"Sacrifice was first requested this round (round {first if first is not None else ctx.round_no})"
        else:
            why = (f"only {len(offers_to_me)} return commitment(s) from different agents are on the table "
                   f"({', '.join(owners) or 'none'}); we need 2")
        mode = A.pick_allowed(ctx, me, [RESTRICTED, STANDARD])
        alt = A.best_alternative(ctx, me)
        counter = ""
        combo = None
        if alt:
            combo = dict(alt["modes"])
            who = ", ".join(A.label(d) for d in A.cand_sacrifices(alt)) or "nobody"
            counter = f" Counter-proposal: {A.cand_label(alt)} (risk {alt.get('risk')}; {who} sacrifices)."
        refusals = st.setdefault("refusals", {})
        refusals[sid] = int(refusals.get(sid, 0)) + 1
        st.setdefault("grievances", []).append(f"S{sid} R{ctx.round_no}: asked to sacrifice {sac_code}")
        return make(REFUSE_SACRIFICE, mode,
                    f"{name} refuses Sacrifice {sac_code}: {sac_pkg.consequence} {why[0].upper() + why[1:]}. "
                    f"We can run {mode} ({A.fmt_res(MODE_PACKAGES[mode].resources())}).{counter}",
                    why, sac_pkg.consequence, refuses_sacrifice=True, proposed_combo=combo)

    # ---- Restricted requested.
    if req_tier == RESTRICTED:
        objected = st.setdefault("objected", [])
        alt_std = None
        for c in ctx.candidates or []:
            if A.tier_of((c.get("modes") or {}).get(me)) == STANDARD and (
                    ctx.override_available or not c.get("requires_override")):
                alt_std = c
                break
        if prev_tier == STANDARD and alt_std is not None and sid not in objected and A.allowed(ctx, prev):
            objected.append(sid)
            return make(OBJECTION, prev,
                        f"{name} objects to {req}: feasible plan {A.cand_label(alt_std)} keeps us at {prev} "
                        f"(risk {alt_std.get('risk')}). Our concern is {prof['main_concern']}.",
                        "A feasible candidate keeps our Standard mode.", proposed_combo=dict(alt_std["modes"]))
        p = MODE_PACKAGES[req]
        if not A.allowed(ctx, req):
            req = A.pick_allowed(ctx, me, [RESTRICTED, STANDARD])
            p = MODE_PACKAGES[req]
        return make(ACCEPT_MODE, req,
                    f"{name} accepts Restricted {req} ({A.fmt_res(p.resources())}, risk {p.risk}) because the numbers "
                    f"show a conflict: {_conflict_text(ctx)}. Recovery debt: {p.consequence}",
                    "Conflict confirmed by validator numbers.")

    # ---- Standard requested, or no request: keep / take the mode.
    mode = req if (req_tier == STANDARD and A.allowed(ctx, req)) else (
        prev if prev and A.allowed(ctx, prev) else A.pick_allowed(ctx, me, [STANDARD, RESTRICTED]))
    p = MODE_PACKAGES[mode]
    verb = "accepts" if req_tier == STANDARD else "holds"
    return make(ACCEPT_MODE, mode, f"{name} {verb} {mode} ({A.fmt_res(p.resources())}, risk {p.risk}). "
                f"Current status: {_conflict_text(ctx) if not A.validation_passed(ctx) else 'plan validates'}.",
                "Mode consistent with the Commander's request." if req_tier else "No request for us this round.")


def department_vote(agent: Any, ctx: AgentContext) -> Vote:
    me = agent.agent_id
    version = A.plan_version(ctx)
    modes = A.plan_modes(ctx)
    mine = modes.get(me)
    declared = (ctx.declared_modes or {}).get(me)
    if not A.validation_passed(ctx):
        reason = "Validation fails: " + (_conflict_text(ctx))
        return Vote(me, version, REJECT, A.short(reason, 300), source=RULE)
    if declared and mine != declared:
        return Vote(me, version, REJECT, f"Plan v{version} gives us {mine}, but we declared {declared}.", source=RULE)
    if A.tier_of(mine) == SACRIFICE:
        owners = A.accepted_owners(ctx, me)
        if len(owners) < 2:
            return Vote(me, version, REJECT, f"We sacrifice ({mine}) with only {len(owners)} accepted return "
                        "commitment(s) from different agents; 2 are required.", source=RULE)
        return Vote(me, version, ACCEPT, f"Validation PASS; our sacrifice {mine} is backed by accepted returns from "
                    f"{', '.join(A.label(o) for o in sorted(owners))}.", source=RULE)
    tot = ((ctx.validation.totals if hasattr(ctx.validation, "totals") else (ctx.validation or {}).get("totals"))
           or A.totals_for(modes)[0])
    risk = A.totals_for(modes)[1]
    return Vote(me, version, ACCEPT, f"Validation PASS; we run {mine} as declared; plan risk {risk}, totals "
                f"{A.fmt_res(tot)} within pool {A.fmt_res(ctx.pool)}.", source=RULE)


# ---------------------------------------------------------------------------
# Commander
# ---------------------------------------------------------------------------
def observe_transcript(state: dict, ctx: AgentContext) -> dict:
    """Accumulate refusals and counter-proposals heard this scenario into the Commander's private state."""
    sc = state.setdefault("scenarios", {}).setdefault(str(ctx.scenario_id),
                                                      {"refusals": [], "counters": {}, "target": None, "history": []})
    seen = {tuple(x) for x in sc["refusals"]}
    for e in ctx.transcript or []:
        sender = e.get("sender")
        if sender not in DEPARTMENTS:
            continue
        rnd = int(e.get("round") or 0)
        if e.get("msg_type") == REFUSE_SACRIFICE and (rnd, sender) not in seen:
            seen.add((rnd, sender))
            sc["refusals"].append([rnd, sender])
        payload = e.get("payload") or {}
        combo = payload.get("proposed_combo") if isinstance(payload, dict) else None
        if not isinstance(combo, dict):
            combo = A.parse_combo(payload.get("counter_combo_label") if isinstance(payload, dict) else None) \
                or (A.parse_combo(e.get("content")) if e.get("msg_type") in (REFUSE_SACRIFICE, OBJECTION,
                                                                            "COUNTEROFFER") else None)
        if combo:
            sc["counters"][sender] = A.combo_label(combo)
    return sc


def refusal_counts(sc: dict) -> dict[str, int]:
    out: dict[str, int] = {}
    for _, d in sc.get("refusals", []):
        out[d] = out.get(d, 0) + 1
    return out


def score_candidate(c: dict, ctx: AgentContext, sc: dict) -> tuple:
    refusals = refusal_counts(sc)
    sacs = A.cand_sacrifices(c)
    modes = c.get("modes") or {}
    impact = sum({STANDARD: 0, RESTRICTED: 1, SACRIFICE: 3}.get(A.tier_of(m), 3) for m in modes.values())
    s = float(impact)
    s += 0 if c.get("template_returns_ok", True) else 3
    s += 2 if c.get("requires_override") else 0
    s += sum(8 for d in sacs if refusals.get(d, 0) >= 2)
    if A.cand_label(c) == sc.get("target") and not any(refusals.get(d, 0) >= 2 for d in sacs):
        s -= 2                                        # stability: give the refusing dept a round to weigh offers
    s -= 1.0 * sum(1 for lab in sc.get("counters", {}).values() if A.norm_label(lab) == A.norm_label(A.cand_label(c)))
    s -= 3 * sum(1 for d in sacs if A.tier_of((ctx.declared_modes or {}).get(d)) == SACRIFICE)
    # a department that already accepted this sacrifice in the previously approved plan (seeded as round 0)
    s -= 3 * sum(1 for d in sacs if (ctx.sacrifice_requests or {}).get(d) == 0)
    s += 0.1 * c.get("risk", 0) - 0.05 * A.min_reserve(c)
    return (round(s, 3), -A.sum_reserve(c))


def choose_target(ctx: AgentContext, sc: dict) -> dict | None:
    cands = [c for c in (ctx.candidates or []) if ctx.override_available or not c.get("requires_override")]
    if not cands:
        return None
    return sorted(cands, key=lambda c: score_candidate(c, ctx, sc))[0]


def commander_offers(ctx: AgentContext, sacs: list[str]) -> tuple[list[CommitmentOffer], list[str]]:
    offers, texts = [], []
    for s in sacs:
        for tpl in A.templates_owned(COMMANDER, s):
            if A.already_offered(ctx, COMMANDER, tpl.template_id):
                continue
            if A.claim_conflict(ctx, tpl.claim, s):
                claim = f"commander_priority_{s.lower()}"
                if any(c.owner == COMMANDER and c.beneficiary == s and c.template_id is None
                       for c in A.commitments(ctx, statuses=("PROPOSED", "ACCEPTED", "REJECTED"))):
                    continue
                promise = f"Commander gives {A.label(s)} second priority next cycle (first priority already pledged)."
                offers.append(CommitmentOffer(beneficiary=s, kind="priority", claim=claim, promise=promise))
                texts.append(promise)
                continue
            offers.append(CommitmentOffer(beneficiary=s, template_id=tpl.template_id))
            texts.append(tpl.promise)
    return offers, texts


def commander_turn(agent: Any, ctx: AgentContext) -> CommanderTurn:
    sc = observe_transcript(agent.state, ctx)
    risk_limit, max_sac = A.policy_limits(ctx)
    v = A.plan_version(ctx)
    status = (ctx.plan or {}).get("status") or ("PASS" if A.validation_passed(ctx) else "FAIL")
    conflict = _conflict_text(ctx)
    target = choose_target(ctx, sc)
    if target is None:
        inf = ctx.infeasibility or {}
        blocking = "; ".join(A.short(b, 100) for b in (inf.get("blocking") or [])[:3]) or "no combination fits"
        sugg = "; ".join(A.short(s, 80) for s in (inf.get("suggestions") or [])[:3]) or "more resources"
        msg = (f"No feasible combination of the 12 packages fits pool {A.fmt_res(ctx.pool)} under risk <= "
               f"{risk_limit} and <= {max_sac} sacrifice(s). Blocking: {blocking}. Requested change: {sugg}.")
        return CommanderTurn(message=A.short(msg, 600), declare_infeasible=True, source=RULE)
    lab = A.cand_label(target)
    sacs = A.cand_sacrifices(target)
    prev_target = sc.get("target")
    refusals = refusal_counts(sc)
    sc["target"] = lab
    sc["history"].append([ctx.round_no, lab])
    offers, offer_texts = commander_offers(ctx, sacs)
    override = bool(ctx.override_available and target.get("requires_override"))
    parts = [f"Plan v{v} {status}: {conflict}." if v else f"Declared demands conflict: {conflict}."]
    if prev_target and prev_target != lab:
        why = [f"{A.label(d)} refused {n}x" for d, n in refusals.items() if n >= 2 and d not in sacs]
        parts.append(f"Switching target from {prev_target} ({'; '.join(why) or 'better balance now'}).")
    parts.append(f"Target {lab}: totals {A.fmt_res(target.get('totals'))}, risk {target.get('risk')}/"
                 f"{28 if override else risk_limit}, reserve {A.fmt_res(target.get('reserve'))}.")
    if sacs:
        parts.append("Sacrifice asked of " + ", ".join(f"{A.label(s)} ({target['modes'][s]})" for s in sacs) + ".")
        if offer_texts:
            parts.append("Commander offers: " + " ".join(offer_texts))
        owners = [t.template_id for s in sacs for t in A.templates_owned_all(s) if t.owner != COMMANDER]
        if owners:
            parts.append("Template owners please offer " + ", ".join(owners) + ".")
    if override:
        parts.append("Invoking Crisis Override (risk <= 28, <= 2 sacrifices): no baseline-policy plan exists.")
    return CommanderTurn(message=A.short(" ".join(parts), 600), target_combo=dict(target["modes"]),
                         mode_requests=dict(target["modes"]), sacrifice_candidates=list(sacs), offers=offers,
                         use_crisis_override=override, source=RULE)
