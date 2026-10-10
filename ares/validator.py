"""Deterministic plan validator for ARES Accord.

Pure functions only: no I/O, no randomness, no LLM calls. Mode package values
are read from `ares.models.MODE_PACKAGES` and never modified.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from ares.models import (
    ACCEPT,
    AGENTS,
    BASELINE_MAX_SACRIFICES,
    BASELINE_RISK_LIMIT,
    COMMANDER,
    DEPARTMENTS,
    MODE_PACKAGES,
    OVERRIDE_MAX_SACRIFICES,
    OVERRIDE_RISK_LIMIT,
    RESOURCE_LABELS,
    RESOURCES,
    SACRIFICE,
    CheckResult,
    Commitment,
    Policy,
    ValidationResult,
    Vote,
)

CHECK_NAMES: tuple[str, ...] = (
    "Mode packages", "Resources", "Risk", "Sacrifice limit",
    "Return agreement", "Commitment backing", "Policy",
)
_SHORT = {"power": "P", "water": "W", "oxygen": "O", "robot": "R", "bandwidth": "B"}
ACCEPTED = "ACCEPTED"


def _label(resource: str) -> str:
    return RESOURCE_LABELS.get(resource, resource)


def _as_commitment(c: Commitment | Mapping[str, Any]) -> Commitment:
    return c if isinstance(c, Commitment) else Commitment.from_dict(c)


def _as_vote(v: Vote | Mapping[str, Any]) -> Vote:
    if isinstance(v, Vote):
        return v
    keys = Vote.__dataclass_fields__.keys()
    return Vote(**{k: v[k] for k in keys if k in v})


def _as_policy(p: Policy | Mapping[str, Any]) -> Policy:
    return p if isinstance(p, Policy) else Policy(**{k: p[k] for k in Policy.__dataclass_fields__ if k in p})


def _is_accepted(c: Commitment) -> bool:
    return str(c.status).upper() == ACCEPTED


def format_reserve(reserve: Mapping[str, int]) -> str:
    return " ".join(f"{_SHORT[r]}{reserve.get(r, 0)}" for r in RESOURCES)


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------
def plan_totals(modes: Mapping[str, str | None]) -> tuple[dict[str, int], int]:
    """Sum of the selected packages' resources and risk. Unknown codes are ignored."""
    totals = {r: 0 for r in RESOURCES}
    risk = 0
    for code in modes.values():
        pkg = MODE_PACKAGES.get(code) if isinstance(code, str) else None
        if pkg is None:
            continue
        for r in RESOURCES:
            totals[r] += getattr(pkg, r)
        risk += pkg.risk
    return totals, risk


def compute_reserve(pool: Mapping[str, int], totals: Mapping[str, int]) -> dict[str, int]:
    return {r: int(pool.get(r, 0)) - int(totals.get(r, 0)) for r in RESOURCES}


def sacrificing_departments(modes: Mapping[str, str | None]) -> list[str]:
    """Departments (in canonical order) whose selected package is a published Sacrifice mode."""
    out = []
    for d in DEPARTMENTS:
        pkg = MODE_PACKAGES.get(modes.get(d)) if isinstance(modes.get(d), str) else None
        if pkg is not None and pkg.department == d and pkg.tier == SACRIFICE:
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
def _check_mode_packages(modes: Mapping[str, str | None],
                         forbidden: set[str]) -> tuple[CheckResult, dict[str, str]]:
    issues: list[str] = []
    valid: dict[str, str] = {}
    for key in modes:
        if key not in DEPARTMENTS:
            issues.append(f"Unknown department key '{key}'")
    for d in DEPARTMENTS:
        code = modes.get(d)
        if not code:
            issues.append(f"Missing mode for {d}")
            continue
        pkg = MODE_PACKAGES.get(code) if isinstance(code, str) else None
        if pkg is None:
            issues.append(f"Unknown mode code '{code}' for {d}")
            continue
        if pkg.department != d:
            issues.append(f"Code {code} belongs to {pkg.department}, not {d}")
            continue
        # Forbidden codes are still real packages: they count towards totals so the
        # resource/risk checks stay informative, but the plan fails this check.
        valid[d] = code
        if code in forbidden:
            issues.append(f"{code} is forbidden by the current event")
    if issues:
        return CheckResult("Mode packages", False, "; ".join(issues)), valid
    labels = ", ".join(MODE_PACKAGES[valid[d]].label for d in DEPARTMENTS)
    return CheckResult("Mode packages", True, f"One published package per department: {labels}"), valid


def _check_resources(totals: Mapping[str, int], pool: Mapping[str, int],
                     reserve: Mapping[str, int]) -> CheckResult:
    over = []
    for r in RESOURCES:
        avail = int(pool.get(r, 0))
        if totals[r] > avail:
            over.append(f"{_label(r)} {totals[r]} > {avail} (over by {totals[r] - avail})")
    if over:
        return CheckResult("Resources", False,
                           f"{len(over)} resource(s) over pool: " + "; ".join(over))
    return CheckResult("Resources", True,
                       f"All {len(RESOURCES)} resources within pool; reserve {format_reserve(reserve)}")


def _check_risk(risk: int, policy: Policy) -> CheckResult:
    if risk > policy.risk_limit:
        return CheckResult("Risk", False, f"Risk {risk} > limit {policy.risk_limit}")
    return CheckResult("Risk", True, f"Risk {risk} <= limit {policy.risk_limit}")


def _check_sacrifice_limit(sacrifices: Sequence[str], modes: Mapping[str, str],
                           policy: Policy) -> CheckResult:
    n = len(sacrifices)
    listed = ", ".join(f"{d} ({modes[d]})" for d in sacrifices)
    if n > policy.max_sacrifices:
        return CheckResult("Sacrifice limit", False,
                           f"{n} Sacrifice modes ({listed}) > limit {policy.max_sacrifices}")
    if n == 0:
        return CheckResult("Sacrifice limit", True, f"No Sacrifice modes (limit {policy.max_sacrifices})")
    return CheckResult("Sacrifice limit", True,
                       f"{n} Sacrifice mode(s) ({listed}) <= limit {policy.max_sacrifices}")


def _check_return_agreement(sacrifices: Sequence[str], modes: Mapping[str, str],
                            commitments: Sequence[Commitment]) -> CheckResult:
    if not sacrifices:
        return CheckResult("Return agreement", True, "No sacrifice in plan")
    failures: list[str] = []
    passes: list[str] = []
    for dept in sacrifices:
        counted: list[Commitment] = []
        ignored: list[str] = []
        for c in commitments:
            if c.beneficiary != dept:
                continue
            if not _is_accepted(c):
                ignored.append(f"{c.id} status {c.status}")
            elif c.owner == dept:
                ignored.append(f"{c.id} self-owned")
            elif c.owner not in AGENTS:
                ignored.append(f"{c.id} unknown owner '{c.owner}'")
            elif not str(c.promise or "").strip():
                ignored.append(f"{c.id} empty promise")
            elif not str(c.expiry or "").strip():
                ignored.append(f"{c.id} no expiry")
            else:
                counted.append(c)
        owners = sorted({c.owner for c in counted})
        head = (f"{dept} ({modes[dept]}) has {len(counted)} accepted return commitment(s) "
                f"from {len(owners)} distinct agent(s)")
        if len(counted) >= 2 and len(owners) >= 2:
            passes.append(f"{head}: {', '.join(owners)}")
        else:
            msg = f"{head}; needs 2 from different agents"
            if ignored:
                msg += f" (not counted: {', '.join(ignored)})"
            failures.append(msg)
    if failures:
        return CheckResult("Return agreement", False, "; ".join(failures))
    return CheckResult("Return agreement", True, "; ".join(passes))


def _check_commitment_backing(sacrifices: Sequence[str], modes: Mapping[str, str],
                              commitments: Sequence[Commitment],
                              reserve: Mapping[str, int]) -> tuple[CheckResult, list[str]]:
    if not sacrifices:
        return CheckResult("Commitment backing", True, "No sacrifice in plan"), []
    relevant = [c for c in commitments if _is_accepted(c) and c.beneficiary in sacrifices]
    issues: list[str] = []
    warnings: list[str] = []
    package_sums: dict[tuple[str, str], int] = defaultdict(int)
    reserve_sums: dict[str, int] = defaultdict(int)

    for c in relevant:
        if c.source == "future" and c.owner in sacrifices:
            warnings.append(f"{c.id}: future-cycle pledge from {c.owner}, which is itself sacrificing "
                            f"({modes[c.owner]}); its delivery capacity is reduced")
        if c.kind == "priority":
            continue
        if c.kind != "resource":
            issues.append(f"{c.id}: unknown commitment kind '{c.kind}'")
            continue
        if c.resource not in RESOURCES:
            issues.append(f"{c.id}: resource commitment names unknown resource '{c.resource}'")
            continue
        if not isinstance(c.amount, int) or isinstance(c.amount, bool) or c.amount <= 0:
            issues.append(f"{c.id}: resource commitment amount must be > 0 (got {c.amount})")
            continue
        if c.source == "package":
            if c.owner not in DEPARTMENTS:
                issues.append(f"{c.id}: package commitment owner {c.owner} is not a department")
            elif c.owner not in modes:
                issues.append(f"{c.id}: owner {c.owner} has no valid selected package")
            else:
                package_sums[(c.owner, c.resource)] += c.amount
        elif c.source == "reserve":
            if c.owner != COMMANDER:
                issues.append(f"{c.id}: reserve commitment owner {c.owner} is not the Commander")
            else:
                reserve_sums[c.resource] += c.amount
        elif c.source == "future":
            pass  # pledged from a future cycle; not counted against this pool
        else:
            issues.append(f"{c.id}: resource commitment has invalid source '{c.source}' "
                          f"(expected package/reserve/future)")

    backed: list[str] = []
    for (owner, res), amount in sorted(package_sums.items()):
        code = modes[owner]
        value = getattr(MODE_PACKAGES[code], res)
        if amount > value:
            issues.append(f"{owner} commits {amount} {_label(res)} from its package but {code} "
                          f"provides only {value} (over by {amount - value})")
        else:
            backed.append(f"{owner} {_label(res)} {amount} <= {value} in {code}")
    for res, amount in sorted(reserve_sums.items()):
        avail = reserve.get(res, 0)
        if amount > avail:
            issues.append(f"Commander reserve commitments {amount} {_label(res)} > reserve {avail} "
                          f"(short by {amount - avail})")
        else:
            backed.append(f"Commander reserve {_label(res)} {amount} <= {avail}")

    claims: dict[str, list[Commitment]] = defaultdict(list)
    for c in relevant:
        if c.claim:
            claims[c.claim].append(c)
    for claim, items in sorted(claims.items()):
        beneficiaries = sorted({c.beneficiary for c in items})
        if len(beneficiaries) > 1:
            ids = ", ".join(f"{c.id} -> {c.beneficiary}" for c in items)
            issues.append(f"Claim conflict on '{claim}': promised to {len(beneficiaries)} beneficiaries ({ids})")

    if issues:
        return CheckResult("Commitment backing", False, "; ".join(issues)), warnings
    detail = f"All {len(relevant)} accepted return commitment(s) are backed"
    if backed:
        detail += f" ({'; '.join(backed)})"
    detail += "; no claim conflicts"
    return CheckResult("Commitment backing", True, detail), warnings


def _check_policy(policy: Policy) -> CheckResult:
    key = (policy.risk_limit, policy.max_sacrifices, bool(policy.crisis_override))
    if key == (BASELINE_RISK_LIMIT, BASELINE_MAX_SACRIFICES, False):
        return CheckResult("Policy", True,
                           f"Baseline policy (risk {BASELINE_RISK_LIMIT}, {BASELINE_MAX_SACRIFICES} Sacrifice)")
    if key == (OVERRIDE_RISK_LIMIT, OVERRIDE_MAX_SACRIFICES, True):
        return CheckResult("Policy", True,
                           f"Crisis Override policy (risk {OVERRIDE_RISK_LIMIT}, "
                           f"{OVERRIDE_MAX_SACRIFICES} Sacrifices)")
    return CheckResult("Policy", False,
                       f"Policy risk_limit={policy.risk_limit}, max_sacrifices={policy.max_sacrifices}, "
                       f"crisis_override={policy.crisis_override} is neither baseline "
                       f"({BASELINE_RISK_LIMIT}/{BASELINE_MAX_SACRIFICES}) nor Crisis Override "
                       f"({OVERRIDE_RISK_LIMIT}/{OVERRIDE_MAX_SACRIFICES})")


def check_returns(modes: Mapping[str, str], commitments: Sequence[Commitment | Mapping[str, Any]],
                  pool: Mapping[str, int]) -> tuple[CheckResult, CheckResult, list[str]]:
    """Run "Return agreement" and "Commitment backing" for a set of (valid) modes.

    Shared by `validate_plan` and the optimizer's template check.
    Returns (return_agreement, commitment_backing, warnings).
    """
    valid = {d: c for d, c in modes.items()
             if d in DEPARTMENTS and c in MODE_PACKAGES and MODE_PACKAGES[c].department == d}
    totals, _ = plan_totals(valid)
    reserve = compute_reserve(pool, totals)
    sacrifices = sacrificing_departments(valid)
    items = [_as_commitment(c) for c in commitments]
    agreement = _check_return_agreement(sacrifices, valid, items)
    backing, warnings = _check_commitment_backing(sacrifices, valid, items, reserve)
    return agreement, backing, warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def validate_plan(modes: Mapping[str, str | None], commitments: Sequence[Commitment | Mapping[str, Any]],
                  pool: Mapping[str, int], policy: Policy | Mapping[str, Any],
                  forbidden_modes: Sequence[str] = ()) -> ValidationResult:
    """Run every check (never short-circuits) so the UI can show all reasons."""
    policy = _as_policy(policy)
    forbidden = {str(f).strip().upper() for f in forbidden_modes or ()}
    mode_check, valid = _check_mode_packages(modes, forbidden)
    totals, risk = plan_totals(valid)
    reserve = compute_reserve(pool, totals)
    sacrifices = sacrificing_departments(valid)
    agreement, backing, warnings = check_returns(valid, commitments, pool)

    checks = [
        mode_check,
        _check_resources(totals, pool, reserve),
        _check_risk(risk, policy),
        _check_sacrifice_limit(sacrifices, valid, policy),
        agreement,
        backing,
        _check_policy(policy),
    ]
    reasons = [f"{c.name}: {c.detail}" for c in checks if not c.passed]
    return ValidationResult(
        status="PASS" if not reasons else "FAIL",
        checks=checks,
        reasons=reasons,
        warnings=warnings,
        totals=totals,
        reserve=reserve,
        risk=risk,
        sacrifices=sacrifices,
    )


def check_votes(votes: Sequence[Vote | Mapping[str, Any]], plan_version: int) -> CheckResult:
    """PASS iff exactly one ACCEPT vote per department, all on `plan_version`."""
    items = [_as_vote(v) for v in votes]
    by_agent: dict[str, list[Vote]] = defaultdict(list)
    for v in items:
        by_agent[v.agent].append(v)
    missing = [d for d in DEPARTMENTS if d not in by_agent]
    duplicates = [f"{d} ({len(by_agent[d])} votes)" for d in DEPARTMENTS if len(by_agent.get(d, [])) > 1]
    unexpected = sorted(a for a in by_agent if a not in DEPARTMENTS)
    rejecting: list[str] = []
    stale: list[str] = []
    for d in DEPARTMENTS:
        for v in by_agent.get(d, []):
            if str(v.decision).upper() != ACCEPT:
                rejecting.append(f"{d} ({v.decision}: {v.reason})" if v.reason else f"{d} ({v.decision})")
            if v.plan_version != plan_version:
                stale.append(f"{d} (voted v{v.plan_version}, current v{plan_version})")
    problems = []
    if missing:
        problems.append("missing: " + ", ".join(missing))
    if duplicates:
        problems.append("duplicate: " + ", ".join(duplicates))
    if unexpected:
        problems.append("non-department voter(s): " + ", ".join(unexpected))
    if rejecting:
        problems.append("rejecting: " + "; ".join(rejecting))
    if stale:
        problems.append("stale: " + "; ".join(stale))
    if problems:
        return CheckResult("Votes", False, f"Votes on plan v{plan_version} incomplete — " + " | ".join(problems))
    return CheckResult("Votes", True, f"{len(DEPARTMENTS)}/{len(DEPARTMENTS)} ACCEPT votes on plan v{plan_version}")


def approval_gate(validation: ValidationResult, votes: Sequence[Vote | Mapping[str, Any]], plan_version: int,
                  current_version: int, round_no: int, min_rounds: int) -> tuple[bool, list[str]]:
    """Final approval gate; the Commander cannot bypass it. Returns (ok, all blocking reasons)."""
    reasons: list[str] = []
    if plan_version != current_version:
        reasons.append(f"Plan v{plan_version} is not the current version v{current_version}")
    if not validation.passed:
        if validation.reasons:
            reasons.extend(f"Validation FAIL — {r}" for r in validation.reasons)
        else:
            reasons.append(f"Validation status {validation.status}")
    vote_check = check_votes(votes, plan_version)
    if not vote_check.passed:
        reasons.append(f"Votes: {vote_check.detail}")
    if round_no < min_rounds:
        reasons.append(f"Round {round_no} < minimum {min_rounds} rounds before approval")
    return not reasons, reasons
