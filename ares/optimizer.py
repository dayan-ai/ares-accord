"""Candidate-plan enumeration and infeasibility diagnosis (decision support only).

The optimizer never approves anything: it lists combinations that satisfy the
hard numeric limits and explains why none exist when the pool is too small.
"""
from __future__ import annotations

from itertools import product
from typing import Any, Mapping, Sequence

from ares.models import (
    DEPARTMENTS,
    MODE_PACKAGES,
    RESOURCE_LABELS,
    RESOURCES,
    RETURN_TEMPLATES,
    Commitment,
    Policy,
    modes_for,
)
from ares.validator import check_returns, compute_reserve, plan_totals, sacrificing_departments

# All 3^4 = 81 combinations, in canonical L, M, F, E order.
ALL_COMBOS: tuple[dict[str, str], ...] = tuple(
    {d: p.code for d, p in zip(DEPARTMENTS, combo)}
    for combo in product(*(modes_for(d) for d in DEPARTMENTS))
)


def combo_label(modes: Mapping[str, str]) -> str:
    return "+".join(str(modes.get(d)) for d in DEPARTMENTS)


def policy_name(policy: Policy) -> str:
    return "Crisis Override" if policy.crisis_override else "baseline policy"


def _limits_text(policy: Policy) -> str:
    return f"<={policy.max_sacrifices} Sacrifice and risk <={policy.risk_limit}"


def _forbidden_set(forbidden_modes: Sequence[str]) -> set[str]:
    return {str(f).strip().upper() for f in forbidden_modes or ()}


def template_commitments(modes: Mapping[str, str], scenario_id: int = 0) -> list[Commitment]:
    """ACCEPTED Commitment objects built from the PDF return templates for each sacrifice."""
    out: list[Commitment] = []
    for dept in sacrificing_departments(modes):
        for t in RETURN_TEMPLATES.get(modes[dept], ()):
            out.append(Commitment(
                id=t.template_id, scenario_id=scenario_id, owner=t.owner, beneficiary=t.beneficiary,
                kind=t.kind, resource=t.resource, amount=t.amount, source=t.source, claim=t.claim,
                promise=t.promise, expiry=t.expiry, status="ACCEPTED", template_id=t.template_id,
            ))
    return out


def _evaluate(modes: dict[str, str], pool: Mapping[str, int]) -> dict[str, Any]:
    totals, risk = plan_totals(modes)
    reserve = compute_reserve(pool, totals)
    return {
        "modes": dict(modes),
        "totals": totals,
        "reserve": reserve,
        "risk": risk,
        "sacrifices": sacrificing_departments(modes),
        "shortfall": {r: -reserve[r] for r in RESOURCES if reserve[r] < 0},
        "label": combo_label(modes),
    }


def _within_limits(ev: Mapping[str, Any], policy: Policy) -> bool:
    return ev["risk"] <= policy.risk_limit and len(ev["sacrifices"]) <= policy.max_sacrifices


def _allowed_combos(pool: Mapping[str, int], forbidden: set[str]) -> list[dict[str, Any]]:
    return [_evaluate(m, pool) for m in ALL_COMBOS if not forbidden.intersection(m.values())]


def _candidate_key(c: Mapping[str, Any]) -> tuple:
    return (c["risk"], len(c["sacrifices"]), -min(c["reserve"].values()), c["label"])


def enumerate_candidates(pool: Mapping[str, int], policy: Policy,
                         forbidden_modes: Sequence[str] = ()) -> list[dict[str, Any]]:
    """Every combination passing resources + risk + sacrifice limit, best first."""
    forbidden = _forbidden_set(forbidden_modes)
    out: list[dict[str, Any]] = []
    for ev in _allowed_combos(pool, forbidden):
        if ev["shortfall"] or not _within_limits(ev, policy):
            continue
        agreement, backing, warnings = check_returns(ev["modes"], template_commitments(ev["modes"]), pool)
        issues = [f"{c.name}: {c.detail}" for c in (agreement, backing) if not c.passed]
        out.append({
            "modes": ev["modes"],
            "totals": ev["totals"],
            "reserve": ev["reserve"],
            "risk": ev["risk"],
            "sacrifices": ev["sacrifices"],
            "template_returns_ok": not issues,
            "template_return_issues": issues,
            "template_return_warnings": warnings,
            "label": ev["label"],
        })
    out.sort(key=_candidate_key)
    return out


def _format_increase(shortfall: Mapping[str, int]) -> str:
    parts = [f"+{shortfall[r]} {RESOURCE_LABELS[r]}" for r in RESOURCES if shortfall.get(r, 0) > 0]
    return " and ".join(parts)


def _nearest(compliant: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not compliant:
        return None
    best = min(compliant, key=lambda e: (sum(e["shortfall"].values()), len(e["shortfall"]),
                                         e["risk"], len(e["sacrifices"]),
                                         -min(e["reserve"].values()), e["label"]))
    return {"modes": best["modes"], "label": best["label"], "shortfall": dict(best["shortfall"]),
            "risk": best["risk"], "sacrifices": list(best["sacrifices"])}


def _blocking_for(policy: Policy, combos: list[dict[str, Any]], forbidden: set[str]) -> list[str]:
    """Human-readable reasons why no combination fits under `policy`."""
    excl = f" (forbidden: {', '.join(sorted(forbidden))})" if forbidden else ""
    compliant = [e for e in combos if _within_limits(e, policy)]
    if not combos:
        return [f"Every combination uses a forbidden mode{excl}"]
    if not compliant:
        min_risk = min(e["risk"] for e in combos)
        min_sac = min(len(e["sacrifices"]) for e in combos)
        return [f"No allowed combination satisfies {_limits_text(policy)}{excl}; the remaining modes need "
                f"risk >= {min_risk} and >= {min_sac} Sacrifice(s)"]
    lines: list[str] = []
    for r in RESOURCES:
        min_short = min(e["shortfall"].get(r, 0) for e in compliant)
        if min_short > 0:
            lines.append(f"Every combination with {_limits_text(policy)}{excl} exceeds {RESOURCE_LABELS[r]} "
                         f"(minimum shortfall {min_short})")
    if not lines and not any(not e["shortfall"] for e in compliant):
        near = _nearest(compliant)
        assert near is not None
        short = ", ".join(f"{RESOURCE_LABELS[r]} {v}" for r, v in near["shortfall"].items())
        lines.append(f"No combination with {_limits_text(policy)}{excl} fits all resources at once "
                     f"(closest: {near['label']} short by {short})")
    return lines


def diagnose_infeasibility(pool: Mapping[str, int], policy: Policy, forbidden_modes: Sequence[str] = (),
                           override_allowed: bool = False) -> dict[str, Any]:
    """Explain feasibility under `policy` and name the external change that would unblock a plan."""
    forbidden = _forbidden_set(forbidden_modes)
    override = Policy.override()
    combos = _allowed_combos(pool, forbidden)
    candidates = enumerate_candidates(pool, policy, forbidden)
    override_candidates = enumerate_candidates(pool, override, forbidden)
    feasible = bool(candidates)
    feasible_with_override = bool(override_candidates) if override_allowed else False
    permissive = override if override_allowed else policy

    blocking: list[str] = []
    if not feasible:
        blocking.extend(_blocking_for(policy, combos, forbidden))
        if override_allowed and not policy.crisis_override and not override_candidates:
            blocking.extend(f"With Crisis Override: {b}" for b in _blocking_for(override, combos, forbidden))

    nearest = _nearest([e for e in combos if _within_limits(e, permissive)])

    suggestions: list[str] = []
    if not feasible:
        if override_candidates and not policy.crisis_override:
            msg = (f"Enable Crisis Override (risk {override.risk_limit}, {override.max_sacrifices} sacrifices) — "
                   f"{len(override_candidates)} plan(s) become feasible (best: {override_candidates[0]['label']})")
            if not override_allowed:
                msg += "; only allowed after a crisis event"
            suggestions.append(msg)
        policies = [policy]
        if override_allowed and not policy.crisis_override and not override_candidates:
            policies.append(override)
        for p in policies:
            near = _nearest([e for e in combos if _within_limits(e, p)])
            if near and near["shortfall"]:
                suggestions.append(f"Add {_format_increase(near['shortfall'])} (would make {near['label']} "
                                   f"feasible under {policy_name(p)})")
        if not (override_allowed and override_candidates):
            for code in sorted(c for c in forbidden if c in MODE_PACKAGES):
                restored = enumerate_candidates(pool, permissive, forbidden - {code})
                if restored:
                    suggestions.append(f"Restore forbidden mode {code} ({len(restored)} plan(s) become feasible "
                                       f"under {policy_name(permissive)}, best: {restored[0]['label']})")
        if not any(_within_limits(e, permissive) for e in combos) and combos:
            min_risk = min(e["risk"] for e in combos)
            min_sac = min(len(e["sacrifices"]) for e in combos)
            if (not permissive.crisis_override and any(_within_limits(e, override) for e in combos)):
                suggestions.append(f"Policy change needed: the remaining modes need risk >= {min_risk} and "
                                   f">= {min_sac} Sacrifice(s); only Crisis Override (risk {override.risk_limit}, "
                                   f"{override.max_sacrifices} sacrifices) permits that")
            else:
                suggestions.append(f"Policy change needed beyond the published limits: the remaining modes need "
                                   f"risk >= {min_risk} and >= {min_sac} Sacrifice(s); lift forbidden modes instead")

    return {
        "feasible": feasible,
        "feasible_with_override": feasible_with_override,
        "candidate_count": len(candidates),
        "override_candidate_count": len(override_candidates) if override_allowed else 0,
        "blocking": blocking,
        "nearest": nearest,
        "suggestions": suggestions,
    }
