"""Verified plan facts for voters, and detection of vote reasons that contradict them.

Pure and deterministic. The same snapshot is (a) shown to every voting agent and (b) used by the
orchestrator to decide whether a REJECT reason states something factually false about the plan.
A contradiction only earns the voter one recorded chance to reconsider; it never changes a vote.
"""
from __future__ import annotations

import re
from typing import Any

from ares.models import AGENT_LABELS, DEPARTMENTS, MODE_PACKAGES, RESOURCES, SACRIFICE, Commitment

_SHORT = {"power": "P", "water": "W", "oxygen": "O", "robot": "R", "bandwidth": "B"}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _fmt(values: dict[str, int] | None) -> str:
    values = values or {}
    return " ".join(f"{_SHORT[r]}{values.get(r, 0)}" for r in RESOURCES)


def plan_snapshot(plan: dict[str, Any] | None, validation: Any, commitments: list[Commitment],
                  pool: dict[str, int], declared: dict[str, str | None] | None) -> dict[str, Any]:
    """Structured, verified facts about the plan version under vote."""
    modes = dict((plan or {}).get("modes") or {})
    sacrificing = [d for d in DEPARTMENTS if MODE_PACKAGES.get(modes.get(d) or "") and
                   MODE_PACKAGES[modes[d]].tier == SACRIFICE]
    checks = [(_get(c, "name"), bool(_get(c, "passed")), _get(c, "detail")) for c in (_get(validation, "checks") or [])]
    compensation = {}
    for d in sacrificing:
        acc = [c for c in commitments if c.beneficiary == d and c.status == "ACCEPTED" and c.owner != d]
        offered = [c for c in commitments if c.beneficiary == d and c.status == "PROPOSED" and c.owner != d]
        owners = sorted({c.owner for c in acc})
        compensation[d] = {"accepted": acc, "offered": offered, "owners": owners, "met": len(owners) >= 2}
    return {
        "version": (plan or {}).get("version"),
        "status": (plan or {}).get("status"),
        "modes": modes,
        "declared": dict(declared or {}),
        "sacrificing": sacrificing,
        "validation_status": _get(validation, "status", "n/a"),
        "checks": checks,
        "totals": _get(validation, "totals") or {},
        "reserve": _get(validation, "reserve") or {},
        "risk": _get(validation, "risk"),
        "pool": dict(pool or {}),
        "compensation": compensation,
    }


def snapshot_text(snap: dict[str, Any], me: str) -> str:
    """Prompt block: every fact a voter needs, with offered vs accepted kept apart."""
    lines = [f"PLAN UNDER VOTE: v{snap['version']} (status {snap['status']})"]
    for d in DEPARTMENTS:
        code = snap["modes"].get(d)
        p = MODE_PACKAGES.get(code or "")
        if p:
            lines.append(f"  {d:<12} {code} {p.tier:<10} {_fmt(p.resources())} risk {p.risk}"
                         + ("   <- YOU" if d == me else ""))
    tot, pool = snap["totals"], snap["pool"]
    lines.append("TOTALS USED/AVAILABLE: " + " ".join(f"{_SHORT[r]}{tot.get(r, 0)}/{pool.get(r, 0)}" for r in RESOURCES)
                 + f" | RESERVE {_fmt(snap['reserve'])} | RISK {snap['risk']}")
    sac = snap["sacrificing"]
    lines.append("SACRIFICING DEPARTMENTS (Sacrifice-tier mode): "
                 + (", ".join(f"{d} ({snap['modes'][d]})" for d in sac) if sac else "NONE"))
    lines.append(f"DETERMINISTIC VALIDATOR RESULT: {snap['validation_status']}")
    for name, passed, detail in snap["checks"]:
        lines.append(f"  [{'PASS' if passed else 'FAIL'}] {name}: {str(detail)[:150]}")
    for d in sac:
        comp = snap["compensation"][d]
        lines.append(f"RETURN COMMITMENTS FOR {d}:")
        for c in comp["accepted"]:
            lines.append(f"  ACCEPTED (counts): {c.id} {c.owner} -> {c.beneficiary}: {c.promise} (expires: {c.expiry})")
        for c in comp["offered"]:
            lines.append(f"  OFFERED, NOT ACCEPTED (does not count): {c.id} {c.owner} -> {c.beneficiary}: {c.promise}")
        lines.append(f"  COMPENSATION REQUIREMENT for {d}: {len(comp['accepted'])} accepted from "
                     f"{len(comp['owners'])} different agent(s) ({', '.join(comp['owners']) or 'none'}) -> "
                     + ("MET" if comp["met"] else "NOT MET (needs 2 from different agents)"))
    mine = snap["modes"].get(me)
    tier = MODE_PACKAGES[mine].tier if mine in MODE_PACKAGES else "?"
    declared = snap["declared"].get(me)
    lines.append(f"YOUR POSITION: you ({me}) run {mine} = {tier}."
                 + (" You ARE a sacrificing department." if me in sac else
                    " You are NOT in Sacrifice mode; the 2-return rule applies only to the sacrificing department(s)"
                    " listed above, not to you."))
    lines.append(f"YOUR DECLARED MODE: {declared or '-'} -> "
                 + ("MATCHES the plan." if declared == mine else "DIFFERS from the plan."))
    return "\n".join(lines)


# --------------------------------------------------------------------------- contradictions
_SELF_SACRIFICE = re.compile(r"\b(i am|i'm|we are|we're|we|i|our|us)\b[^.;]{0,30}\bsacrific", re.I)
_MAKING_SACRIFICE = re.compile(r"\b(making|make|bear|bearing|take|taking)\b[^.;]{0,12}\bsacrific", re.I)
_MISSING_RETURNS = re.compile(
    r"(without|lack|lacking|no |zero|missing|insufficient|not have|don't have|do not have|only (one|1)|fewer than)"
    r"[^.;]{0,60}(return|commitment|compensation)|(return|commitment|compensation)s?[^.;]{0,40}"
    r"(missing|lacking|insufficient|not (been )?accepted|unaccepted)", re.I)
_VALIDATION_FAIL = re.compile(
    r"(validation fail|fails validation|failed validation|exceed|over (the )?pool|over (the )?limit|"
    r"above (the )?limit|violat|infeasible|not feasible)", re.I)
_MODE_MISMATCH = re.compile(r"(mode|code)[^.;]{0,40}(not (the )?(mode )?(we|i) declared|mismatch|differ|wrong)|"
                            r"not (the|our|my) declared mode", re.I)


def find_contradictions(voter: str, reason: str, snap: dict[str, Any]) -> list[str]:
    """Facts that a REJECT reason gets wrong. Empty list = no detectable factual error (genuine objection)."""
    text = reason or ""
    out: list[str] = []
    mine = snap["modes"].get(voter)
    sac = snap["sacrificing"]
    in_sacrifice = voter in sac
    if not in_sacrifice and (_SELF_SACRIFICE.search(text) or _MAKING_SACRIFICE.search(text)):
        tier = MODE_PACKAGES[mine].tier if mine in MODE_PACKAGES else "?"
        who = ", ".join(f"{d} ({snap['modes'][d]})" for d in sac) or "none"
        out.append(f"You are not sacrificing: plan v{snap['version']} assigns you {mine} ({tier}). "
                   f"Sacrificing department(s): {who}.")
    if _MISSING_RETURNS.search(text):
        if not sac:
            out.append("No department is in Sacrifice mode in this plan, so no return commitments are required.")
        elif all(snap["compensation"][d]["met"] for d in sac):
            parts = []
            for d in sac:
                comp = snap["compensation"][d]
                parts.append(f"{d}: " + ", ".join(f"{c.id} from {c.owner} (ACCEPTED)" for c in comp["accepted"]))
            out.append("Every sacrificing department already has 2 ACCEPTED return commitments from different "
                       "agents - " + "; ".join(parts) + ".")
    if snap["validation_status"] == "PASS" and _VALIDATION_FAIL.search(text):
        tot, pool = snap["totals"], snap["pool"]
        out.append("The deterministic validator returned PASS on every check: totals "
                   + " ".join(f"{_SHORT[r]}{tot.get(r, 0)}/{pool.get(r, 0)}" for r in RESOURCES)
                   + f", risk {snap['risk']} within the limit.")
    if snap["declared"].get(voter) == mine and _MODE_MISMATCH.search(text):
        out.append(f"Plan v{snap['version']} assigns you {mine}, exactly the mode you declared.")
    return out


def label(agent: str) -> str:
    return AGENT_LABELS.get(agent, agent)
