"""Compact prompt builders (target < ~1,200 input tokens; Groq free tier = 8K TPM)."""
from __future__ import annotations

from typing import Any

from ares.models import (COMMANDER, DEPARTMENTS, MODE_PACKAGES, RETURN_TEMPLATES, SACRIFICE, AgentContext, mode_code)
from ares.agents import analysis as A
from ares.agents.profiles import COLONY_SIZE, INJURED
from ares import factcheck

MAX_CANDIDATES = 5
MAX_TRANSCRIPT = 8
LINE_CHARS = 140
# covered by the structured plan/commitment blocks -> skipped to stay under ~1,200 tokens
SKIP_TYPES = {"SYSTEM", "PLAN_DRAFT", "VALIDATION", "VOTE_CALL", "COMMITMENT_ACCEPTED", "COMMITMENT_REJECTED"}

DEPT_SCHEMA = ('{"msg_type": "PROPOSAL|OBJECTION|COUNTEROFFER|ACCEPT_MODE|REFUSE_SACRIFICE|ACCEPT_SACRIFICE", '
               '"mode": "<one of your codes>", "message": "<= 3 sentences citing numbers", "reason": "...", '
               '"consequence": "what you lose in this mode", "offer_template_ids": [], "custom_offers": [], '
               '"accept_commitment_ids": [], "reject_commitment_ids": [], "refuses_sacrifice": false, '
               '"counter_combo_label": null}')
VOTE_SCHEMA = '{"vote": "ACCEPT|REJECT", "reason": "one sentence citing numbers"}'
CMD_SCHEMA = ('{"message": "<= 4 sentences citing numbers", "target_label": "<a candidate label>", '
              '"mode_requests": {"DEPT": "code"}, "offer_template_ids": [], "use_crisis_override": false, '
              '"declare_infeasible": false}')
REPAIR_SUFFIX = ("\n\nYOUR PREVIOUS REPLY WAS NOT A VALID JSON OBJECT WITH THE REQUIRED FIELDS. "
                 "Reply with ONLY the JSON object, no prose, no code fences.")


def _header(ctx: AgentContext) -> str:
    risk, sac = A.policy_limits(ctx)
    ov = "available (risk<=28, 2 sacrifices) - EVENT only" if ctx.override_available else "not available"
    lines = [f"ROUND {ctx.round_no}/{ctx.max_rounds} | phase {ctx.phase} | scenario {ctx.scenario_kind} | "
             f"min rounds before approval {ctx.min_rounds}",
             f"POLICY: risk <= {risk}, sacrifices <= {sac}; Crisis Override {ov}",
             f"POOL ({A.RES_LEGEND}): {A.fmt_res(ctx.pool)}"]
    if ctx.event is not None:
        lines.append(f"EVENT: {A.short(getattr(ctx.event, 'name', ''), 60)} - "
                     f"{A.short(getattr(ctx.event, 'description', ''), 140)}")
    if ctx.forbidden_modes:
        lines.append(f"FORBIDDEN MODES: {', '.join(ctx.forbidden_modes)}")
    return "\n".join(lines)


def _plan_block(ctx: AgentContext) -> str:
    out = ["DECLARED MODES: " + ", ".join(f"{d}={(ctx.declared_modes or {}).get(d) or '-'}" for d in DEPARTMENTS)]
    if ctx.plan:
        modes = A.plan_modes(ctx)
        tot, risk = A.totals_for(modes)
        v = ctx.validation
        status = getattr(v, "status", None) or (v or {}).get("status") if v is not None else "n/a"
        out.append(f"CURRENT PLAN v{A.plan_version(ctx)} [{ctx.plan.get('status')}]: {A.combo_label(modes)} | "
                   f"totals {A.fmt_res(tot)} | risk {risk} | validation {status}")
        for r in A.validation_reasons(ctx)[:3]:
            out.append(f"  - {A.short(r, 150)}")
    return "\n".join(out)


def _candidates_block(ctx: AgentContext, order: list[dict] | None = None) -> str:
    cands = order if order is not None else list(ctx.candidates or [])
    if not cands:
        inf = ctx.infeasibility or {}
        lines = ["CANDIDATES: none feasible."]
        if inf:
            lines.append("  blocking: " + "; ".join(A.short(b, 90) for b in (inf.get("blocking") or [])[:3]))
            lines.append("  suggestions: " + "; ".join(A.short(s, 70) for s in (inf.get("suggestions") or [])[:3]))
        return "\n".join(lines)
    lines = [f"FEASIBLE CANDIDATES (validator search, top {min(len(cands), MAX_CANDIDATES)} of {len(cands)}):"]
    for c in cands[:MAX_CANDIDATES]:
        sac = ",".join(A.cand_sacrifices(c)) or "none"
        flags = ("returns_ok" if c.get("template_returns_ok", True) else "returns_issue") + \
            (" REQUIRES_OVERRIDE" if c.get("requires_override") else "")
        lines.append(f"  {A.cand_label(c)} risk {c.get('risk')} sacrifices[{sac}] reserve "
                     f"{A.fmt_res(c.get('reserve'))} {flags}")
    return "\n".join(lines)


def _commitments_block(ctx: AgentContext, me: str) -> str:
    cs = [c for c in (ctx.commitments or []) if c.status in ("PROPOSED", "ACCEPTED")]
    if not cs:
        return "COMMITMENTS: none yet."
    lines = ["COMMITMENTS (ACCEPTED = agreed by the beneficiary and counts; OFFERED = on the table, not yet accepted):"]
    mine = [c for c in cs if c.beneficiary == me or c.owner == me]
    rest = [c for c in cs if c not in mine]
    for c in (mine + rest)[:10]:
        tag = " <- TO YOU" if c.beneficiary == me else (" (offered by you)" if c.owner == me else "")
        state = "ACCEPTED" if c.status == "ACCEPTED" else "OFFERED"
        lines.append(f"  {c.id} {c.owner}->{c.beneficiary} {state}{tag}: {A.short(c.promise, 80)}")
    return "\n".join(lines)


def _requested_or_declared(ctx: AgentContext, dept: str) -> str | None:
    return (ctx.commander_requests or {}).get(dept) or (ctx.declared_modes or {}).get(dept)


def _options_block(ctx: AgentContext, me: str) -> str:
    """Deterministic what-if for each of my codes, holding the others at their requested/declared modes."""
    risk_lim, sac_lim = A.policy_limits(ctx)
    others = {d: _requested_or_declared(ctx, d) for d in DEPARTMENTS if d != me}
    if any(v is None for v in others.values()):
        return ""
    lines = ["YOUR OPTIONS (others at their requested/declared modes; computed by the validator math):"]
    for tier in ("STANDARD", "RESTRICTED", "SACRIFICE"):
        code = A.codes_for(me)[tier]
        modes = {**others, me: code}
        over = A.over_pool(modes, ctx.pool)
        _, risk = A.totals_for(modes)
        nsac = sum(1 for c in modes.values() if A.tier_of(c) == SACRIFICE)
        issues = list(over) + ([f"risk {risk} > {risk_lim}"] if risk > risk_lim else []) + \
            ([f"{nsac} sacrifices > {sac_lim}"] if nsac > sac_lim else [])
        lines.append(f"  {code} {tier}: " + ("FITS the pool and limits" if not issues else "BREAKS: " + "; ".join(issues)))
    return "\n".join(lines)


def _returns_asked_block(ctx: AgentContext, me: str) -> str:
    """Return templates I own for departments the Commander asked to sacrifice this round."""
    asked = []
    for d, code in (ctx.commander_requests or {}).items():
        if d == me or A.tier_of(code) != SACRIFICE:
            continue
        for t in RETURN_TEMPLATES.get(code, ()):
            if t.owner == me and not A.already_offered(ctx, me, t.template_id):
                asked.append(f"{t.template_id} -> {d}: {A.short(t.promise, 70)}")
    if not asked:
        return ""
    return ("RETURNS YOU ARE ASKED TO PROVIDE (the sacrificing department needs 2 from different agents; offer via "
            "offer_template_ids unless you have a factual reason not to): " + "; ".join(asked))


def _transcript_block(ctx: AgentContext) -> str:
    rows = [e for e in (ctx.transcript or []) if e.get("msg_type") not in SKIP_TYPES][-MAX_TRANSCRIPT:]
    if not rows:
        return "RECENT TRANSCRIPT: (empty)"
    lines = ["RECENT TRANSCRIPT:"]
    for e in rows:
        lines.append(f"  R{e.get('round')} {e.get('sender')} {e.get('msg_type')}: {A.short(e.get('content'), LINE_CHARS)}")
    return "\n".join(lines)


def _memory_block(memory: list[dict]) -> str:
    rows = memory[-4:]
    if not rows:
        return "YOUR MEMORY: (nothing yet)"
    return "YOUR MEMORY (private): " + " | ".join(
        f"S{m.get('scenario')} R{m.get('round')} {m.get('kind')}: {A.short(m.get('decided'), 60)}" for m in rows)


# ---------------------------------------------------------------------------
# Department
# ---------------------------------------------------------------------------
def _dept_system(agent: Any) -> str:
    me, prof = agent.agent_id, agent.profile
    codes = A.codes_for(me)
    c = "/".join(codes[t] for t in ("STANDARD", "RESTRICTED", "SACRIFICE"))
    return (
        f"You are the {prof['name']} department agent ({me}) of Mars colony ARES ({COLONY_SIZE} people, "
        f"{INJURED} injured) in a resource crisis council.\nRole: {prof['role']} Goals: {'; '.join(prof['goals'])}. "
        f"Constraints: {'; '.join(prof['constraints'])}. Main concern: {prof['main_concern']}.\n"
        "HARD RULES:\n"
        f"1. Pick exactly ONE of YOUR codes ({c}). Never invent or edit numbers; cite only numbers given.\n"
        "2. PROPOSAL phase (round 1 only): request your Standard mode and say what you would lose in Restricted and in "
        "Sacrifice. In NEGOTIATION rounds do NOT go back to Standard if YOUR OPTIONS shows it BREAKS the pool.\n"
        "3. Accept Restricted when the numbers show a conflict (resources over pool, risk over limit).\n"
        "4. Sacrifice is a severe loss. NEVER accept it in the round it is first requested of you: refuse "
        "(msg_type REFUSE_SACRIFICE, refuses_sacrifice true, mode = Restricted) and you may counter-propose a "
        "candidate label (counter_combo_label) where another department sacrifices.\n"
        "5. In a later round accept Sacrifice (msg_type ACCEPT_SACRIFICE, mode = your Sacrifice code, "
        "accept_commitment_ids = the offers to you) only if >= 2 return commitments to you from DIFFERENT agents "
        "are on the table; otherwise keep refusing.\n"
        "6. If another department is asked to sacrifice and you own a matching return template, offer it "
        "(offer_template_ids).\n"
        "7. You may object or disagree, but stay factual. The deterministic validator and the 4 department votes "
        "decide approval; the Commander cannot approve alone.\n"
        "8. Restricted is NOT Sacrifice. Only refuse Sacrifice (REFUSE_SACRIFICE) if YOU are asked to run your Sacrifice "
        "code this round.\n"
        f"Reply with ONE JSON object only (no prose), schema:\n{DEPT_SCHEMA}")


def department_prompt(agent: Any, ctx: AgentContext) -> tuple[str, str]:
    me = agent.agent_id
    sac = mode_code(me, SACRIFICE)
    lines = [_header(ctx), "YOUR MODES:"]
    lines += [f"  {A.package_line(p)}" for p in (A.codes_for(me)[t] for t in ("STANDARD", "RESTRICTED", "SACRIFICE"))]
    recv = RETURN_TEMPLATES.get(sac, ())
    if recv:
        lines.append(f"RETURNS YOU WOULD NEED IF YOU SACRIFICE ({sac}): " +
                     "; ".join(f"{t.template_id} from {t.owner}: {A.short(t.promise, 70)}" for t in recv))
    owned = [t for tpls in RETURN_TEMPLATES.values() for t in tpls if t.owner == me]
    if owned:
        lines.append("TEMPLATES YOU OWN (offer only when that department is asked to sacrifice): " +
                     "; ".join(f"{t.template_id} -> {t.beneficiary}: {A.short(t.promise, 70)}" for t in owned))
    lines.append(_plan_block(ctx))
    req = (ctx.commander_requests or {}).get(me)
    if ctx.commander_requests:
        lines.append("COMMANDER REQUESTS THIS ROUND: " +
                     ", ".join(f"{d}={c}" for d, c in ctx.commander_requests.items()))
    if req and A.tier_of(req) == SACRIFICE:
        first = A.first_sacrifice_round(ctx, me)
        offers = A.distinct_owner_offers(ctx, me)
        if A.may_accept_sacrifice_now(ctx, me):
            lines.append(f"YOU ARE ASKED TO SACRIFICE ({req}); first asked in round {first}. Offers to you from "
                         f"{len(offers)} different agent(s): {', '.join(c.id for c in offers) or 'none'}.")
        else:
            lines.append(f"YOU ARE ASKED TO SACRIFICE ({req}) FOR THE FIRST TIME THIS ROUND - rule 4 applies.")
    elif req:
        cur = (ctx.declared_modes or {}).get(me)
        keep = " (keep your current mode)" if req == cur else ""
        lines.append(f"YOU ARE ASKED TO RUN {req} ({A.tier_of(req)}){keep}. You are NOT asked to sacrifice this round.")
    cur = (ctx.declared_modes or {}).get(me)
    if cur and ctx.phase != "PROPOSAL":
        lines.append(f"YOUR CURRENT DECLARED MODE: {cur} ({A.tier_of(cur)}).")
    for block in (_returns_asked_block(ctx, me), _options_block(ctx, me) if ctx.phase != "PROPOSAL" else ""):
        if block:
            lines.append(block)
    lines += [_commitments_block(ctx, me), _candidates_block(ctx), _transcript_block(ctx),
              _memory_block(agent.memory), "Respond now with the JSON object."]
    return _dept_system(agent), "\n".join(lines)


VOTE_RULES = (
    "Base your vote ONLY on the VERIFIED PLAN FACTS below (computed by the deterministic validator from the stored "
    "plan and commitments). Tiers: Standard, Restricted and Sacrifice are different; Restricted is NOT Sacrifice. "
    "The 2-return rule applies only to the department(s) listed as SACRIFICING. Only ACCEPTED commitments count; "
    "OFFERED ones do not. Vote REJECT if the validator result is FAIL, if your plan mode differs from your declared "
    "mode, or if YOU are sacrificing and your compensation requirement is NOT MET. You may also REJECT for a genuine "
    "reason (e.g. the mission impact on your department or an unfair return agreement), but never state a fact that "
    "contradicts the verified facts. Your vote is independent; a PASS does not oblige you to accept.")


def _vote_facts(ctx: AgentContext, me: str) -> str:
    snap = factcheck.plan_snapshot(ctx.plan, ctx.validation, list(ctx.commitments or []), ctx.pool,
                                   ctx.declared_modes)
    return "VERIFIED PLAN FACTS:\n" + factcheck.snapshot_text(snap, me)


def vote_prompt(agent: Any, ctx: AgentContext) -> tuple[str, str]:
    me, prof = agent.agent_id, agent.profile
    system = (f"You are the {prof['name']} department agent ({me}) of Mars colony ARES voting on plan version "
              f"v{A.plan_version(ctx)}. Main concern: {prof['main_concern']}. {VOTE_RULES} "
              f"Reply with ONE JSON object only: {VOTE_SCHEMA}")
    lines = [_header(ctx), _vote_facts(ctx, me), _memory_block(agent.memory), "Respond now with the JSON object."]
    return system, "\n".join(lines)


def reconsider_prompt(agent: Any, ctx: AgentContext, original_reason: str, corrections: list[str]) -> tuple[str, str]:
    me, prof = agent.agent_id, agent.profile
    system = (f"You are the {prof['name']} department agent ({me}) of Mars colony ARES. You voted REJECT on plan "
              f"v{A.plan_version(ctx)}, but the deterministic validator found that your stated reason contradicts "
              f"the verified plan facts. {VOTE_RULES} Reconsider: vote again on the same version. Keep REJECT if you "
              "still object for a reason that is true; otherwise vote on the facts. "
              f"Reply with ONE JSON object only: {VOTE_SCHEMA}")
    lines = [_header(ctx), _vote_facts(ctx, me),
             f"YOUR ORIGINAL VOTE: REJECT - {A.short(original_reason, 240)}",
             "FACT CHECK (validator):"] + [f"  - {c}" for c in corrections] + ["Respond now with the JSON object."]
    return system, "\n".join(lines)


# ---------------------------------------------------------------------------
# Commander
# ---------------------------------------------------------------------------
def commander_prompt(agent: Any, ctx: AgentContext, ranked: list[dict], sc: dict) -> tuple[str, str]:
    prof = agent.profile
    system = (
        f"You are the Commander of Mars colony ARES ({COLONY_SIZE} people, {INJURED} injured). Role: {prof['role']}\n"
        "RULES: You NEVER approve plans (a deterministic validator + 4 department votes do). Choose target_label "
        "from FEASIBLE CANDIDATES only; balance mission impact, refusals heard, returns_ok and reserve. Request "
        "each department's mode from that target (mode_requests). Offer your own return templates "
        "(offer_template_ids) for departments asked to sacrifice. A department refuses Sacrifice the first round it "
        "is asked; if it refused twice or made a strong counter-proposal, consider a candidate where someone else "
        "sacrifices instead of repeating the request. use_crisis_override true only if override is available and "
        "the target REQUIRES_OVERRIDE. declare_infeasible true only if there are no candidates (then cite blocking "
        f"constraints and suggestions).\nReply with ONE JSON object only (no prose), schema:\n{CMD_SCHEMA}")
    refusals = {}
    for _, d in sc.get("refusals", []):
        refusals[d] = refusals.get(d, 0) + 1
    cmd_tpls = [t for tpls in RETURN_TEMPLATES.values() for t in tpls if t.owner == COMMANDER]
    lines = [_header(ctx), _plan_block(ctx), _candidates_block(ctx, ranked),
             "YOUR TEMPLATES: " + "; ".join(f"{t.template_id} -> {t.beneficiary}: {A.short(t.promise, 60)}"
                                            for t in cmd_tpls),
             "REFUSALS HEARD: " + (", ".join(f"{d} x{n}" for d, n in refusals.items()) or "none"),
             "COUNTER-PROPOSALS: " + (", ".join(f"{d}: {lab}" for d, lab in sc.get("counters", {}).items()) or "none"),
             f"YOUR PREVIOUS TARGET: {sc.get('target') or 'none'}",
             _commitments_block(ctx, COMMANDER), _transcript_block(ctx), _memory_block(agent.memory),
             "Respond now with the JSON object."]
    return system, "\n".join(lines)
