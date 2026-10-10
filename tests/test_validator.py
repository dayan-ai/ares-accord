"""Unit tests for ares/validator.py (pure, no network)."""
from __future__ import annotations

import dataclasses

import pytest

from ares.models import (
    BASELINE_POOL, COMMANDER, DEPARTMENTS, ENGINEERING, FOOD, LIFE_SUPPORT, MEDICAL,
    RETURN_TEMPLATES, Commitment, Policy, ValidationResult, Vote,
)
from ares.validator import (
    CHECK_NAMES, approval_gate, check_votes, plan_totals, validate_plan,
)

POOL = dict(BASELINE_POOL)
BASE = Policy.baseline()
OVR = Policy.override()

_TEMPLATES = {t.template_id: t for group in RETURN_TEMPLATES.values() for t in group}


def modes(l: str, m: str, f: str, e: str) -> dict[str, str]:
    return {LIFE_SUPPORT: l, MEDICAL: m, FOOD: f, ENGINEERING: e}


def tmpl(template_id: str, status: str = "ACCEPTED", **overrides) -> Commitment:
    t = _TEMPLATES[template_id]
    c = Commitment(id=f"S1-{template_id}", scenario_id=1, owner=t.owner, beneficiary=t.beneficiary,
                   kind=t.kind, resource=t.resource, amount=t.amount, source=t.source, claim=t.claim,
                   promise=t.promise, expiry=t.expiry, status=status, template_id=template_id)
    return dataclasses.replace(c, **overrides)


def custom(cid: str, owner: str, beneficiary: str, status: str = "ACCEPTED", **kw) -> Commitment:
    base = dict(kind="priority", resource=None, amount=0, source=None, claim=None,
                promise=f"{owner} supports {beneficiary}", expiry="End of next planning cycle")
    base.update(kw)
    return Commitment(id=cid, scenario_id=1, owner=owner, beneficiary=beneficiary, status=status, **base)


def check(result: ValidationResult, name: str):
    return next(c for c in result.checks if c.name == name)


PATH_A = modes("L3", "M2", "F2", "E2")
PATH_B = modes("L2", "M2", "F2", "E3")
PATH_A_RETURNS = [tmpl("L3-ENG"), tmpl("L3-CMD")]
PATH_B_RETURNS = [tmpl("E3-CMD"), tmpl("E3-FOOD")]


# ---------------------------------------------------------------------------
# Baseline paths
# ---------------------------------------------------------------------------
def test_plan_totals_path_a_matches_pdf():
    totals, risk = plan_totals(PATH_A)
    assert totals == {"power": 79, "water": 50, "oxygen": 57, "robot": 26, "bandwidth": 17}
    assert risk == 24


def test_plan_totals_ignores_unknown_codes():
    totals, risk = plan_totals({LIFE_SUPPORT: "L9", MEDICAL: "M1"})
    assert totals["power"] == 28 and risk == 1


def test_path_a_passes_with_template_returns():
    r = validate_plan(PATH_A, PATH_A_RETURNS, POOL, BASE)
    assert r.passed, r.reasons
    assert [c.name for c in r.checks] == list(CHECK_NAMES)
    assert r.reasons == [] and r.risk == 24
    assert r.sacrifices == [LIFE_SUPPORT]
    assert r.reserve == {"power": 0, "water": 2, "oxygen": 2, "robot": 0, "bandwidth": 0}
    assert "reserve P0 W2 O2 R0 B0" in check(r, "Resources").detail
    assert "ENGINEERING Robot time 4 <= 18 in E2" in check(r, "Commitment backing").detail


def test_path_b_passes_with_template_returns():
    r = validate_plan(PATH_B, PATH_B_RETURNS, POOL, BASE)
    assert r.passed, r.reasons
    assert r.totals == {"power": 79, "water": 50, "oxygen": 59, "robot": 26, "bandwidth": 16}
    assert r.sacrifices == [ENGINEERING]
    assert "Commander reserve Water 2 <= 2" in check(r, "Commitment backing").detail


def test_all_restricted_fails_on_four_resources():
    r = validate_plan(modes("L2", "M2", "F2", "E2"), [], POOL, BASE)
    assert not r.passed
    assert r.totals == {"power": 83, "water": 51, "oxygen": 60, "robot": 30, "bandwidth": 18}
    res = check(r, "Resources")
    assert not res.passed
    assert res.detail.startswith("4 resource(s) over pool")
    for frag in ("Power 83 > 79 (over by 4)", "Oxygen 60 > 59 (over by 1)",
                 "Robot time 30 > 26 (over by 4)", "Bandwidth 18 > 17 (over by 1)"):
        assert frag in res.detail
    assert "Water" not in res.detail
    assert check(r, "Risk").passed and check(r, "Return agreement").detail == "No sacrifice in plan"


def test_all_standard_fails():
    r = validate_plan(modes("L1", "M1", "F1", "E1"), [], POOL, BASE)
    assert not r.passed
    assert not check(r, "Resources").passed
    assert "Power 112 > 79 (over by 33)" in check(r, "Resources").detail
    assert r.risk == 4


# ---------------------------------------------------------------------------
# Mode packages / policy / limits
# ---------------------------------------------------------------------------
def test_forbidden_mode_fails_but_all_checks_still_run():
    r = validate_plan(PATH_A, PATH_A_RETURNS, POOL, BASE, forbidden_modes=["L3"])
    assert not r.passed
    assert [c.name for c in r.checks] == list(CHECK_NAMES)
    assert "L3 is forbidden" in check(r, "Mode packages").detail
    assert check(r, "Resources").passed and check(r, "Return agreement").passed


@pytest.mark.parametrize("bad_modes, fragment", [
    ({LIFE_SUPPORT: "L3", MEDICAL: "M2", FOOD: "F2"}, "Missing mode for ENGINEERING"),
    (modes("L3", "M2", "F2", "E9"), "Unknown mode code 'E9'"),
    (modes("M2", "M2", "F2", "E2"), "Code M2 belongs to MEDICAL, not LIFE_SUPPORT"),
    ({**PATH_A, "COMMANDER": "L1"}, "Unknown department key 'COMMANDER'"),
])
def test_invalid_mode_packages(bad_modes, fragment):
    r = validate_plan(bad_modes, [], POOL, BASE)
    assert not r.passed
    mp = check(r, "Mode packages")
    assert not mp.passed and fragment in mp.detail
    assert len(r.checks) == 7


def test_invalid_codes_excluded_from_totals():
    r = validate_plan(modes("M2", "M2", "F2", "E2"), [], POOL, BASE)
    assert r.totals["power"] == 21 + 15 + 20


def test_two_sacrifices_under_baseline_policy_fail():
    m = modes("L3", "M2", "F2", "E3")
    rets = PATH_A_RETURNS + PATH_B_RETURNS
    r = validate_plan(m, rets, POOL, BASE)
    assert not r.passed
    sl = check(r, "Sacrifice limit")
    assert not sl.passed and "2 Sacrifice modes" in sl.detail and "> limit 1" in sl.detail
    assert check(r, "Risk").detail == "Risk 28 > limit 24"
    # Same plan is valid under Crisis Override.
    assert validate_plan(m, rets, POOL, OVR).passed


@pytest.mark.parametrize("policy, ok", [
    (Policy(24, 1, False), True), (Policy(28, 2, True), True),
    (Policy(26, 1, False), False), (Policy(28, 2, False), False), (Policy(24, 1, True), False),
])
def test_policy_consistency(policy, ok):
    r = validate_plan(PATH_A, PATH_A_RETURNS, POOL, policy)
    assert check(r, "Policy").passed is ok


# ---------------------------------------------------------------------------
# Return agreement
# ---------------------------------------------------------------------------
def test_return_agreement_missing():
    r = validate_plan(PATH_A, [], POOL, BASE)
    ra = check(r, "Return agreement")
    assert not ra.passed
    assert ("LIFE_SUPPORT (L3) has 0 accepted return commitment(s) from 0 distinct agent(s); "
            "needs 2 from different agents") in ra.detail


def test_return_agreement_only_one():
    r = validate_plan(PATH_A, [tmpl("L3-ENG")], POOL, BASE)
    ra = check(r, "Return agreement")
    assert not ra.passed and "has 1 accepted return commitment(s) from 1 distinct agent(s)" in ra.detail


def test_return_agreement_two_from_same_owner():
    rets = [tmpl("L3-ENG"), custom("S1-X", ENGINEERING, LIFE_SUPPORT)]
    ra = check(validate_plan(PATH_A, rets, POOL, BASE), "Return agreement")
    assert not ra.passed and "has 2 accepted return commitment(s) from 1 distinct agent(s)" in ra.detail


def test_return_agreement_self_owned_not_counted():
    rets = [custom("S1-SELF", LIFE_SUPPORT, LIFE_SUPPORT), tmpl("L3-CMD")]
    ra = check(validate_plan(PATH_A, rets, POOL, BASE), "Return agreement")
    assert not ra.passed and "S1-SELF self-owned" in ra.detail


def test_return_agreement_proposed_not_accepted():
    rets = [tmpl("L3-ENG"), tmpl("L3-CMD", status="PROPOSED")]
    r = validate_plan(PATH_A, rets, POOL, BASE)
    ra = check(r, "Return agreement")
    assert not ra.passed and "status PROPOSED" in ra.detail
    assert any(reason.startswith("Return agreement:") for reason in r.reasons)


def test_return_agreement_empty_promise_or_expiry():
    rets = [tmpl("L3-ENG", promise="  "), tmpl("L3-CMD", expiry="")]
    ra = check(validate_plan(PATH_A, rets, POOL, BASE), "Return agreement")
    assert not ra.passed and "empty promise" in ra.detail and "no expiry" in ra.detail


def test_commitment_dicts_are_accepted():
    r = validate_plan(PATH_A, [c.to_dict() for c in PATH_A_RETURNS], POOL, BASE)
    assert r.passed


# ---------------------------------------------------------------------------
# Commitment backing
# ---------------------------------------------------------------------------
def test_reserve_over_commit_fails():
    pool = {**POOL, "water": 51}           # reserve water = 51 - 50 = 1 < 2
    r = validate_plan(PATH_B, PATH_B_RETURNS, pool, BASE)
    assert check(r, "Resources").passed
    cb = check(r, "Commitment backing")
    assert not cb.passed and "Commander reserve commitments 2 Water > reserve 1" in cb.detail


def test_reserve_commitment_requires_commander():
    rets = [tmpl("E3-CMD", owner=FOOD, id="S1-BAD"), tmpl("E3-FOOD"), custom("S1-Y", COMMANDER, ENGINEERING)]
    cb = check(validate_plan(PATH_B, rets, POOL, BASE), "Commitment backing")
    assert not cb.passed and "reserve commitment owner FOOD is not the Commander" in cb.detail


def test_package_over_commit_fails():
    rets = [tmpl("L3-ENG", amount=20), tmpl("L3-CMD")]
    cb = check(validate_plan(PATH_A, rets, POOL, BASE), "Commitment backing")
    assert not cb.passed and "ENGINEERING commits 20 Robot time from its package but E2 provides only 18" in cb.detail


def test_package_commitments_sum_per_owner_and_resource():
    rets = [tmpl("L3-ENG", amount=10), tmpl("L3-ENG", id="S1-dup", amount=10), tmpl("L3-CMD")]
    cb = check(validate_plan(PATH_A, rets, POOL, BASE), "Commitment backing")
    assert not cb.passed and "commits 20 Robot time" in cb.detail


def test_package_commitment_owner_must_be_department():
    rets = [tmpl("L3-ENG", owner=COMMANDER, id="S1-PKG"), custom("S1-E", ENGINEERING, LIFE_SUPPORT)]
    cb = check(validate_plan(PATH_A, rets, POOL, BASE), "Commitment backing")
    assert not cb.passed and "package commitment owner COMMANDER is not a department" in cb.detail


@pytest.mark.parametrize("override, fragment", [
    ({"resource": "fuel"}, "unknown resource 'fuel'"),
    ({"amount": 0}, "amount must be > 0"),
    ({"source": None}, "invalid source"),
])
def test_bad_resource_commitments(override, fragment):
    rets = [tmpl("L3-ENG", **override), tmpl("L3-CMD")]
    cb = check(validate_plan(PATH_A, rets, POOL, BASE), "Commitment backing")
    assert not cb.passed and fragment in cb.detail


def test_claim_conflict_l3_m3_under_override():
    m = modes("L3", "M3", "F2", "E2")
    rets = [tmpl("L3-ENG"), tmpl("L3-CMD"), tmpl("M3-LS"), tmpl("M3-CMD")]
    r = validate_plan(m, rets, POOL, OVR)
    assert not r.passed
    failed = [c.name for c in r.checks if not c.passed]
    assert failed == ["Commitment backing"]
    assert "Claim conflict on 'commander_next_cycle_first_priority'" in check(r, "Commitment backing").detail
    # Replacing the duplicate Commander promise with a different agent's return resolves it.
    fixed = [tmpl("L3-ENG"), tmpl("L3-CMD"), tmpl("M3-LS"), custom("S1-F", FOOD, MEDICAL)]
    assert validate_plan(m, fixed, POOL, OVR).passed


def test_commitments_for_non_sacrificing_beneficiary_are_ignored():
    rets = PATH_A_RETURNS + [tmpl("M3-CMD")]   # Medical is not sacrificing in Path A
    assert validate_plan(PATH_A, rets, POOL, BASE).passed


def test_future_pledge_from_sacrificing_owner_is_warning_only():
    m = modes("L2", "M2", "F3", "E3")
    rets = [tmpl("F3-ENG"), tmpl("F3-CMD"), tmpl("E3-CMD"), tmpl("E3-FOOD")]
    r = validate_plan(m, rets, POOL, OVR)
    assert r.passed, r.reasons
    assert len(r.warnings) == 1 and "future-cycle pledge from ENGINEERING" in r.warnings[0]


def test_no_sacrifice_backing_detail():
    r = validate_plan(modes("L2", "M2", "F2", "E2"), [], POOL, BASE)
    assert check(r, "Commitment backing").detail == "No sacrifice in plan"


# ---------------------------------------------------------------------------
# Votes and approval gate
# ---------------------------------------------------------------------------
def votes(version: int = 3, **overrides: tuple[str, int]) -> list[Vote]:
    out = []
    for d in DEPARTMENTS:
        decision, v = overrides.get(d, ("ACCEPT", version))
        out.append(Vote(agent=d, plan_version=v, decision=decision, reason="ok" if decision == "ACCEPT" else "no"))
    return out


def test_votes_pass():
    c = check_votes(votes(3), 3)
    assert c.passed and c.name == "Votes" and "4/4 ACCEPT votes on plan v3" in c.detail


def test_votes_missing_voter():
    c = check_votes([v for v in votes(3) if v.agent != FOOD], 3)
    assert not c.passed and "missing: FOOD" in c.detail


def test_votes_reject():
    c = check_votes(votes(3, MEDICAL=("REJECT", 3)), 3)
    assert not c.passed and "rejecting: MEDICAL" in c.detail


def test_votes_stale_version():
    c = check_votes(votes(3, ENGINEERING=("ACCEPT", 2)), 3)
    assert not c.passed and "stale: ENGINEERING (voted v2, current v3)" in c.detail


def test_votes_duplicate_and_non_department():
    vs = votes(3) + [Vote(FOOD, 3, "ACCEPT", "again"), Vote(COMMANDER, 3, "ACCEPT", "boss")]
    c = check_votes(vs, 3)
    assert not c.passed and "duplicate: FOOD" in c.detail and "non-department voter(s): COMMANDER" in c.detail


def test_approval_gate_blocks_before_min_rounds_then_allows():
    val = validate_plan(PATH_A, PATH_A_RETURNS, POOL, BASE)
    ok, reasons = approval_gate(val, votes(3), 3, 3, round_no=2, min_rounds=3)
    assert not ok and reasons == ["Round 2 < minimum 3 rounds before approval"]
    ok, reasons = approval_gate(val, votes(3), 3, 3, round_no=3, min_rounds=3)
    assert ok and reasons == []


def test_approval_gate_returns_all_reasons():
    val = validate_plan(PATH_A, [], POOL, BASE)          # fails return agreement
    ok, reasons = approval_gate(val, votes(2, FOOD=("REJECT", 2)), 2, 3, round_no=1, min_rounds=3)
    assert not ok
    text = " | ".join(reasons)
    assert "Plan v2 is not the current version v3" in text
    assert "Validation FAIL — Return agreement" in text
    assert "Votes:" in text and "rejecting: FOOD" in text
    assert "Round 1 < minimum 3" in text
    assert len(reasons) >= 4


def test_approval_gate_stale_votes_block_current_plan():
    val = validate_plan(PATH_B, PATH_B_RETURNS, POOL, BASE)
    ok, reasons = approval_gate(val, votes(4), 5, 5, round_no=4, min_rounds=3)
    assert not ok and any("stale" in r for r in reasons)
