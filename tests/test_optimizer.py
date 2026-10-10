"""Unit tests for ares/optimizer.py (pure, no network)."""
from __future__ import annotations

import pytest

from ares.models import BASELINE_POOL, LIFE_SUPPORT, Policy
from ares.optimizer import (
    ALL_COMBOS, combo_label, diagnose_infeasibility, enumerate_candidates, template_commitments,
)
from ares.validator import validate_plan

POOL = dict(BASELINE_POOL)
BASE = Policy.baseline()
OVR = Policy.override()

PRACTICE_EVENTS = [  # (resource, delta, expected override candidate count)
    ("power", -4, 3), ("water", -3, 5), ("oxygen", -3, 2), ("robot", -4, 1), ("bandwidth", -2, 3),
]


def shifted(resource: str, delta: int) -> dict[str, int]:
    return {**POOL, resource: POOL[resource] + delta}


def test_all_81_combos():
    assert len(ALL_COMBOS) == 81
    assert len({combo_label(m) for m in ALL_COMBOS}) == 81


def test_baseline_returns_exactly_two_paths():
    cands = enumerate_candidates(POOL, BASE)
    assert {c["label"] for c in cands} == {"L3+M2+F2+E2", "L2+M2+F2+E3"}
    for c in cands:
        assert set(c) >= {"modes", "totals", "reserve", "risk", "sacrifices", "template_returns_ok",
                          "template_return_issues", "label"}
        assert c["risk"] == 24 and len(c["sacrifices"]) == 1
        assert c["template_returns_ok"] and c["template_return_issues"] == []
        assert min(c["reserve"].values()) >= 0


def test_candidates_sorted():
    cands = enumerate_candidates(shifted("water", -3), OVR)
    keys = [(c["risk"], len(c["sacrifices"]), -min(c["reserve"].values())) for c in cands]
    assert keys == sorted(keys)


def test_label_order():
    assert combo_label({"ENGINEERING": "E2", "FOOD": "F2", "MEDICAL": "M2", "LIFE_SUPPORT": "L3"}) == "L3+M2+F2+E2"


@pytest.mark.parametrize("resource, delta, expected", PRACTICE_EVENTS)
def test_practice_events_need_override(resource, delta, expected):
    pool = shifted(resource, delta)
    assert enumerate_candidates(pool, BASE) == []
    cands = enumerate_candidates(pool, OVR)
    assert len(cands) == expected
    assert all(c["risk"] <= 28 and len(c["sacrifices"]) <= 2 for c in cands)


@pytest.mark.parametrize("resource, delta, expected", PRACTICE_EVENTS)
def test_template_flag_agrees_with_validator(resource, delta, expected):
    pool = shifted(resource, delta)
    for c in enumerate_candidates(pool, OVR):
        r = validate_plan(c["modes"], template_commitments(c["modes"]), pool, OVR)
        assert r.passed is c["template_returns_ok"], (c["label"], r.reasons)


def test_template_issue_reserve_after_water_event():
    cands = {c["label"]: c for c in enumerate_candidates(shifted("water", -3), OVR)}
    c = cands["L3+M2+F2+E3"]
    assert not c["template_returns_ok"]
    assert any("reserve 0" in i for i in c["template_return_issues"])


def test_template_issue_claim_conflict_l3_m3():
    cands = {c["label"]: c for c in enumerate_candidates(shifted("oxygen", -3), OVR)}
    c = cands["L3+M3+F2+E2"]
    assert not c["template_returns_ok"]
    assert any("Claim conflict" in i for i in c["template_return_issues"])


def test_template_future_pledge_warning():
    cands = {c["label"]: c for c in enumerate_candidates(shifted("bandwidth", -2), OVR)}
    c = cands["L2+M2+F3+E3"]
    assert c["template_returns_ok"] and c["template_return_warnings"]


def test_forbidden_modes_filter():
    cands = enumerate_candidates(POOL, BASE, forbidden_modes=["E3"])
    assert [c["label"] for c in cands] == ["L3+M2+F2+E2"]
    assert enumerate_candidates(POOL, BASE, forbidden_modes=["e3", "l3"]) == []


def test_template_commitments_are_accepted_templates():
    cs = template_commitments({"LIFE_SUPPORT": "L3", "MEDICAL": "M2", "FOOD": "F2", "ENGINEERING": "E2"})
    assert [c.template_id for c in cs] == ["L3-ENG", "L3-CMD"]
    assert all(c.status == "ACCEPTED" and c.beneficiary == LIFE_SUPPORT for c in cs)


# ---------------------------------------------------------------------------
# diagnose_infeasibility
# ---------------------------------------------------------------------------
def test_diagnose_baseline_feasible():
    d = diagnose_infeasibility(POOL, BASE)
    assert d["feasible"] and not d["feasible_with_override"]
    assert d["blocking"] == [] and d["suggestions"] == []
    assert d["nearest"]["shortfall"] == {}


def test_diagnose_power_event_with_override():
    d = diagnose_infeasibility(shifted("power", -4), BASE, override_allowed=True)
    assert not d["feasible"] and d["feasible_with_override"]
    assert any("exceeds Power" in b and "<=1 Sacrifice and risk <=24" in b for b in d["blocking"])
    assert any(s.startswith("Enable Crisis Override") and "3 plan(s)" in s for s in d["suggestions"])
    assert d["nearest"]["shortfall"] == {}
    assert d["nearest"]["risk"] <= 28


def test_diagnose_power_event_without_override():
    d = diagnose_infeasibility(shifted("power", -4), BASE, override_allowed=False)
    assert not d["feasible"] and d["feasible_with_override"] is False
    assert d["nearest"]["shortfall"] == {"power": 4}
    assert any("Add +4 Power" in s and "baseline policy" in s for s in d["suggestions"])
    # The suggested increase really does unblock the nearest plan.
    fixed = enumerate_candidates(POOL, BASE)
    assert d["nearest"]["label"] in {c["label"] for c in fixed}


@pytest.mark.parametrize("resource, delta, expected", PRACTICE_EVENTS)
def test_diagnose_every_practice_event(resource, delta, expected):
    d = diagnose_infeasibility(shifted(resource, delta), BASE, override_allowed=True)
    assert not d["feasible"] and d["feasible_with_override"]
    assert d["override_candidate_count"] == expected
    assert d["blocking"]


def test_diagnose_infeasible_even_with_override():
    pool = shifted("power", -20)
    d = diagnose_infeasibility(pool, BASE, override_allowed=True)
    assert not d["feasible"] and not d["feasible_with_override"]
    assert any(b.startswith("With Crisis Override:") for b in d["blocking"])
    near = d["nearest"]
    assert near["shortfall"] and any("under Crisis Override" in s for s in d["suggestions"])
    bumped = {r: pool[r] + near["shortfall"].get(r, 0) for r in pool}
    assert near["label"] in {c["label"] for c in enumerate_candidates(bumped, OVR)}


def test_diagnose_forbidden_modes_need_policy_change():
    forbidden = ["L1", "L2", "M1", "M2"]       # forces L3 + M3 -> 2 sacrifices
    d = diagnose_infeasibility(POOL, BASE, forbidden_modes=forbidden, override_allowed=False)
    assert not d["feasible"]
    assert any("No allowed combination satisfies" in b for b in d["blocking"])
    assert any("Policy change needed" in s and "Crisis Override" in s for s in d["suggestions"])
    assert d["nearest"] is None


def test_diagnose_suggests_restoring_forbidden_mode():
    d = diagnose_infeasibility(POOL, BASE, forbidden_modes=["E3", "L3"])
    assert not d["feasible"]
    assert any(s.startswith("Restore forbidden mode") for s in d["suggestions"])
