"""Pure helpers that read an AgentContext (shared by prompts, sanitiser and rule policy)."""
from __future__ import annotations

import re
from typing import Any, Iterable

from ares.models import (AGENT_LABELS, DEPARTMENTS, MODE_PACKAGES, RESOURCES, RETURN_TEMPLATES, SACRIFICE,
                         STANDARD, RESTRICTED, AgentContext, Commitment, CommitmentTemplate, mode_code, modes_for)

TIER_RANK = {STANDARD: 0, RESTRICTED: 1, SACRIFICE: 2}
ABBR = {"power": "P", "water": "W", "oxygen": "O", "robot": "R", "bandwidth": "B"}
RES_LEGEND = "P=power W=water O=oxygen R=robot time B=bandwidth"
LABEL_RE = re.compile(r"\bL[1-3]\s*\+\s*M[1-3]\s*\+\s*F[1-3]\s*\+\s*E[1-3]\b", re.I)
ACTIVE = ("PROPOSED", "ACCEPTED")


def label(agent: str) -> str:
    return AGENT_LABELS.get(agent, agent)


def codes_for(dept: str) -> dict[str, str]:
    return {p.tier: p.code for p in modes_for(dept)}


def tier_of(code: str | None) -> str | None:
    p = MODE_PACKAGES.get(code or "")
    return p.tier if p else None


def owns_code(dept: str, code: str | None) -> bool:
    p = MODE_PACKAGES.get(code or "")
    return bool(p and p.department == dept)


def allowed(ctx: AgentContext, code: str | None) -> bool:
    return bool(code in MODE_PACKAGES and code not in (ctx.forbidden_modes or []))


def pick_allowed(ctx: AgentContext, dept: str, prefs: Iterable[str]) -> str:
    codes = codes_for(dept)
    for tier in prefs:
        if allowed(ctx, codes.get(tier)):
            return codes[tier]
    for tier in (STANDARD, RESTRICTED, SACRIFICE):
        if allowed(ctx, codes.get(tier)):
            return codes[tier]
    return codes[STANDARD]


def fmt_res(d: dict[str, int] | None) -> str:
    d = d or {}
    return " ".join(f"{ABBR[r]}{d.get(r, 0)}" for r in RESOURCES)


def totals_for(modes: dict[str, str | None]) -> tuple[dict[str, int], int]:
    tot = {r: 0 for r in RESOURCES}
    risk = 0
    for code in modes.values():
        p = MODE_PACKAGES.get(code or "")
        if p:
            for r in RESOURCES:
                tot[r] += getattr(p, r)
            risk += p.risk
    return tot, risk


def over_pool(modes: dict[str, str | None], pool: dict[str, int]) -> list[str]:
    tot, _ = totals_for(modes)
    return [f"{r} {tot[r]}>{pool.get(r, 0)}" for r in RESOURCES if tot[r] > pool.get(r, 0)]


def combo_label(modes: dict[str, str | None] | None) -> str:
    modes = modes or {}
    return "+".join(str(modes.get(d) or "?") for d in DEPARTMENTS)


def norm_label(s: Any) -> str:
    return re.sub(r"\s+", "", str(s or "")).upper()


def cand_sacrifices(c: dict) -> list[str]:
    sac = c.get("sacrifices")
    if sac is None:
        sac = [d for d, m in (c.get("modes") or {}).items() if tier_of(m) == SACRIFICE]
    return list(sac)


def cand_label(c: dict) -> str:
    return c.get("label") or combo_label(c.get("modes"))


def candidate_by_label(ctx: AgentContext, lab: Any) -> dict | None:
    key = norm_label(lab)
    if not key:
        return None
    for c in ctx.candidates or []:
        if norm_label(cand_label(c)) == key:
            return c
    return None


def candidate_for_modes(ctx: AgentContext, modes: dict | None) -> dict | None:
    if not modes:
        return None
    for c in ctx.candidates or []:
        if all((c.get("modes") or {}).get(d) == modes.get(d) for d in DEPARTMENTS):
            return c
    return None


def parse_combo(text: Any) -> dict[str, str] | None:
    """Parse 'L2+M2+F2+E3' into a full combination of published codes, else None."""
    m = LABEL_RE.search(str(text or ""))
    if not m:
        return None
    parts = re.split(r"\s*\+\s*", m.group(0).upper())
    return {d: p for d, p in zip(DEPARTMENTS, parts) if owns_code(d, p)} if len(parts) == 4 else None


def min_reserve(c: dict) -> int:
    res = c.get("reserve") or {}
    return min(res.values()) if res else 0


def sum_reserve(c: dict) -> int:
    return sum((c.get("reserve") or {}).values())


# ---------------------------------------------------------------------------
# Validation / plan
# ---------------------------------------------------------------------------
def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def validation_passed(ctx: AgentContext) -> bool:
    return _get(ctx.validation, "status") == "PASS"


def validation_reasons(ctx: AgentContext) -> list[str]:
    return [str(r) for r in (_get(ctx.validation, "reasons") or [])]


def plan_version(ctx: AgentContext) -> int:
    try:
        return int((ctx.plan or {}).get("version") or 0)
    except (TypeError, ValueError):
        return 0


def plan_modes(ctx: AgentContext) -> dict[str, str]:
    return dict((ctx.plan or {}).get("modes") or {})


def policy_limits(ctx: AgentContext) -> tuple[int, int]:
    return int(_get(ctx.policy, "risk_limit", 24)), int(_get(ctx.policy, "max_sacrifices", 1))


# ---------------------------------------------------------------------------
# Commitments
# ---------------------------------------------------------------------------
def commitments(ctx: AgentContext, beneficiary: str | None = None, owner: str | None = None,
                statuses: Iterable[str] = ACTIVE) -> list[Commitment]:
    st = set(statuses)
    return [c for c in (ctx.commitments or []) if c.status in st
            and (beneficiary is None or c.beneficiary == beneficiary) and (owner is None or c.owner == owner)]


def distinct_owner_offers(ctx: AgentContext, dept: str) -> list[Commitment]:
    """One active commitment per distinct owner (owner != dept); ACCEPTED preferred, then templates."""
    best: dict[str, Commitment] = {}
    for c in commitments(ctx, beneficiary=dept):
        if c.owner == dept or not (c.promise or "").strip():
            continue
        cur = best.get(c.owner)
        key = (c.status != "ACCEPTED", c.template_id is None, c.id)
        if cur is None or key < (cur.status != "ACCEPTED", cur.template_id is None, cur.id):
            best[c.owner] = c
    return sorted(best.values(), key=lambda c: c.id)


def accepted_owners(ctx: AgentContext, dept: str) -> set[str]:
    return {c.owner for c in commitments(ctx, beneficiary=dept, statuses=("ACCEPTED",))
            if c.owner != dept and (c.promise or "").strip() and (c.expiry or "").strip()}


def templates_owned(owner: str, beneficiary: str) -> list[CommitmentTemplate]:
    sac = mode_code(beneficiary, SACRIFICE)
    return [t for t in RETURN_TEMPLATES.get(sac, ()) if t.owner == owner and t.beneficiary == beneficiary]


def template_by_id(tid: Any) -> CommitmentTemplate | None:
    for tpls in RETURN_TEMPLATES.values():
        for t in tpls:
            if t.template_id == tid:
                return t
    return None


def already_offered(ctx: AgentContext, owner: str, template_id: str) -> bool:
    return any(c.owner == owner and c.template_id == template_id and c.status in ("PROPOSED", "ACCEPTED", "REJECTED")
               for c in (ctx.commitments or []))


def claim_conflict(ctx: AgentContext, claim: str | None, beneficiary: str) -> bool:
    return bool(claim) and any(c.claim == claim and c.beneficiary != beneficiary and c.status in ACTIVE
                               for c in (ctx.commitments or []))


# ---------------------------------------------------------------------------
# Sacrifice bookkeeping
# ---------------------------------------------------------------------------
def sacrifice_requested(ctx: AgentContext, dept: str) -> bool:
    return tier_of((ctx.commander_requests or {}).get(dept)) == SACRIFICE


def first_sacrifice_round(ctx: AgentContext, dept: str) -> int | None:
    r = (ctx.sacrifice_requests or {}).get(dept)
    return int(r) if r is not None else None


def may_accept_sacrifice_now(ctx: AgentContext, dept: str) -> bool:
    """Protocol: never accept Sacrifice in the round it is first requested."""
    first = first_sacrifice_round(ctx, dept)
    return first is not None and first < ctx.round_no


def sacrifice_targets(ctx: AgentContext) -> list[str]:
    out = []
    for d in DEPARTMENTS:
        if sacrifice_requested(ctx, d) or tier_of((ctx.declared_modes or {}).get(d)) == SACRIFICE:
            out.append(d)
    return out


def best_alternative(ctx: AgentContext, dept: str) -> dict | None:
    """Feasible candidate where `dept` does not sacrifice (for counter-proposals)."""
    pool = [c for c in (ctx.candidates or []) if dept not in cand_sacrifices(c)
            and (ctx.override_available or not c.get("requires_override"))]
    if not pool:
        return None
    return sorted(pool, key=lambda c: (not c.get("template_returns_ok", True),
                                       TIER_RANK.get(tier_of((c.get("modes") or {}).get(dept)), 3),
                                       len(cand_sacrifices(c)), c.get("risk", 99), -min_reserve(c)))[0]


def package_line(code: str) -> str:
    p = MODE_PACKAGES[code]
    return f"{p.code} {p.tier.title()}: {fmt_res(p.resources())} risk {p.risk} - {p.consequence}"


def short(text: Any, n: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: max(0, n - 3)].rstrip() + "..."


def templates_owned_all(beneficiary: str) -> list[CommitmentTemplate]:
    """All PDF return templates that would back `beneficiary`'s Sacrifice mode."""
    return list(RETURN_TEMPLATES.get(mode_code(beneficiary, SACRIFICE), ()))
