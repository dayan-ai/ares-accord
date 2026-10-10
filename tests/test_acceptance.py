"""End-to-end acceptance tests for ARES Accord (qa-testing).

Real rule-based agents (`ares.agents.build_agents("rule-based")`), a temporary SQLite file per test and no
network. Each test docstring lists the PRD requirement IDs it covers (see PRD.md / docs/REQUIREMENTS_TRACEABILITY.md).
Expected numbers come from the PDF (guide v2.1) and PRD Appendix A, never from the implementation under test.
"""
from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path

import pytest

from ares import optimizer
from ares.config import Settings
from ares.events import parse_event
from ares.models import (
    ACCEPT, BASELINE_POOL, COMMANDER, DEPARTMENTS, MODE_PACKAGES, RESOURCES, SACRIFICE, Commitment, Policy,
)
from ares.service import Service
from ares.storage import Store
from ares.validator import plan_totals, validate_plan

ROOT = Path(__file__).resolve().parent.parent
PDF_PATHS = {  # PDF p6 / PRD Appendix A: the only two valid baseline plans
    "L3+M2+F2+E2": {"LIFE_SUPPORT": "L3", "MEDICAL": "M2", "FOOD": "F2", "ENGINEERING": "E2"},
    "L2+M2+F2+E3": {"LIFE_SUPPORT": "L2", "MEDICAL": "M2", "FOOD": "F2", "ENGINEERING": "E3"},
}
AGENT_SOURCES = ("llm", "rule-based")
TERMINAL = ("APPROVED", "INFEASIBLE", "DEADLOCK", "TIMEOUT")

# PDF p8 practice events, official-style JSON (starter `impact` dict) and free text.
PRACTICE_EVENTS = [
    ("power", -4,
     {"event_id": "QA-P4", "event_name": "Solar aftershock", "description": "Available Power decreases by 4.",
      "impact": {"power_reduction": 4}, "requires_replan": True},
     "Solar aftershock: Available Power decreases by 4"),
    ("water", -3,
     {"event_id": "QA-W3", "event_name": "Water contamination", "description": "Available Water decreases by 3.",
      "impact": {"water_reduction": 3}, "requires_replan": True},
     "Water contamination: Available Water decreases by 3"),
    ("oxygen", -3,
     {"event_id": "QA-O3", "event_name": "Secondary oxygen leak", "description": "Available Oxygen decreases by 3.",
      "impact": {"oxygen_reduction": 3}, "requires_replan": True},
     "Secondary oxygen leak: Available Oxygen decreases by 3"),
    ("robot", -4,
     {"event_id": "QA-R4", "event_name": "Rover actuator failure", "description": "Robot time decreases by 4.",
      "impact": {"robot_time_reduction": 4}, "requires_replan": True},
     "Rover actuator failure: Available Robot time decreases by 4"),
    ("bandwidth", -2,
     {"event_id": "QA-B2", "event_name": "Relay interference", "description": "Bandwidth decreases by 2.",
      "impact": {"bandwidth_reduction": 2}, "requires_replan": True},
     "Relay interference: Available Bandwidth decreases by 2"),
]


# ---------------------------------------------------------------------------------------------- helpers
def svc(tmp_path, **kw) -> Service:
    s = Settings(db_path=str(tmp_path / "t.db"), default_provider="rule-based", **kw)
    return Service(settings=s, store=Store(s.db_path))


def baseline(service: Service, pool=None, max_rounds: int = 6) -> dict:
    return service.start_baseline(dict(pool or BASELINE_POOL), "rule-based", max_rounds=max_rounds)


def label(modes: dict) -> str:
    return "+".join(modes[d] for d in DEPARTMENTS)


def assert_plan_independently_valid(service: Service, plan: dict, scenario: dict) -> None:
    """Re-run the deterministic validator on an approved plan using stored data only."""
    comms = [c for c in service.store.commitments(plan["scenario_id"]) if c.id in plan["commitment_ids"]]
    forbidden = (scenario.get("event") or {}).get("cumulative_forbidden_modes", [])
    policy = Policy(**plan["policy"]) if isinstance(plan["policy"], dict) else plan["policy"]
    v = validate_plan(plan["modes"], comms, scenario["pool"], policy, forbidden)
    assert v.passed, f"approved plan v{plan['version']} fails independent validation: {v.reasons}"
    totals, risk = plan_totals(plan["modes"])
    for r in RESOURCES:
        assert totals[r] <= scenario["pool"][r], f"{r}: {totals[r]} > pool {scenario['pool'][r]}"
    assert risk <= policy.risk_limit
    sac = [d for d, c in plan["modes"].items() if MODE_PACKAGES[c].tier == SACRIFICE]
    assert len(sac) <= policy.max_sacrifices
    for d, code in plan["modes"].items():
        assert MODE_PACKAGES[code].department == d
        assert code not in forbidden


def assert_fresh_votes(service: Service, plan: dict) -> None:
    valid = service.store.votes(plan["id"], valid_only=True)
    assert len(valid) == 4
    assert {v["agent"] for v in valid} == set(DEPARTMENTS)
    assert all(v["decision"] == ACCEPT and v["plan_version"] == plan["version"] for v in valid)


def msgs_of(service: Service, scenario_id: int) -> list[dict]:
    return service.transcript(scenario_id=scenario_id)


def approval_round(service: Service, scenario_id: int) -> int:
    """Round in which the APPROVAL was recorded (a plan's own `round` is the round it was drafted)."""
    appr = [m for m in msgs_of(service, scenario_id) if m["msg_type"] == "APPROVAL"]
    assert len(appr) == 1
    return appr[0]["round"]


# ------------------------------------------------------------------------------- 1. baseline acceptance
def test_baseline_rule_based_run_meets_pdf_protocol(tmp_path):
    """FR-01, FR-03, FR-05..FR-18, FR-21, FR-25, FR-26, AG-04, AG-05, AG-06, FR-43, AG-09:
    baseline pool -> APPROVED on one of the two PDF paths after >= 3 rounds, with refusal, revision,
    complete p5 message types, a backed sacrifice and 4 ACCEPT votes on the approved version."""
    s = svc(tmp_path)
    sc = baseline(s)
    assert sc["status"] == "APPROVED", sc["result_reason"]
    st = s.state()
    proto = st["protocol"]
    assert proto["rounds_before_approval"] >= 3
    assert sc["round"] >= 3
    assert proto["revised_or_rejected"] is True
    assert proto["sacrifice_refused"] is True

    plan = st["approved_plan"]
    assert plan["status"] == "APPROVED"
    assert approval_round(s, sc["id"]) >= 3
    assert label(plan["modes"]) in PDF_PATHS
    assert plan["modes"] == PDF_PATHS[label(plan["modes"])]
    # exactly one own package per dept, values equal the immutable PDF table
    assert set(plan["modes"]) == set(DEPARTMENTS)
    totals, risk = plan_totals(plan["modes"])
    assert plan["totals"] == totals and plan["risk"] == risk <= 24
    for d, code in plan["modes"].items():
        assert MODE_PACKAGES[code].department == d
    sacrificing = [d for d, c in plan["modes"].items() if MODE_PACKAGES[c].tier == SACRIFICE]
    assert len(sacrificing) <= 1
    comms = {c.id: c for c in s.store.commitments(plan["scenario_id"])}
    for d in sacrificing:
        backing = [comms[i] for i in plan["commitment_ids"] if comms[i].beneficiary == d]
        accepted = [c for c in backing if c.status == "ACCEPTED"]
        assert len(accepted) >= 2
        assert len({c.owner for c in accepted}) >= 2
        assert all(c.owner != d and c.promise.strip() and c.expiry.strip() for c in accepted)
    assert_plan_independently_valid(s, plan, sc)
    assert_fresh_votes(s, plan)

    msgs = msgs_of(s, sc["id"])
    types = {m["msg_type"] for m in msgs}
    assert "BRIEFING" in types and "PROPOSAL" in types and "APPROVAL" in types
    assert types & {"OBJECTION", "CONFLICT"}
    assert types & {"COUNTEROFFER", "REQUEST"}
    assert msgs[0]["msg_type"] in ("SYSTEM", "BRIEFING")
    first_briefing = next(i for i, m in enumerate(msgs) if m["msg_type"] == "BRIEFING")
    first_proposal = next(i for i, m in enumerate(msgs) if m["msg_type"] == "PROPOSAL")
    assert first_briefing < first_proposal  # Commander publishes the state first (FR-05)
    for m in msgs:
        assert m["sender"] and m["msg_type"] and m["round"] is not None
    proposals = [m for m in msgs if m["msg_type"] == "PROPOSAL"]
    assert {m["sender"] for m in proposals} == set(DEPARTMENTS)
    for m in proposals:  # FR-06: request Standard and state mode, consequence, reason
        assert MODE_PACKAGES[m["payload"]["mode"]].tier == "STANDARD"
        assert m["payload"]["consequence"] and m["payload"].get("reason") is not None
    commitments = [m for m in msgs if m["msg_type"] == "COMMITMENT"]
    assert commitments
    for m in commitments:
        for k in ("owner", "beneficiary", "promise", "expiry"):
            assert m["payload"].get(k), f"COMMITMENT payload lacks {k}"
    votes = [m for m in msgs if m["msg_type"] == "VOTE"]
    assert len(votes) >= 4
    for m in votes:
        assert m["payload"]["decision"] in ("ACCEPT", "REJECT")
        assert isinstance(m["payload"]["plan_version"], int) and m["payload"]["reason"]
    approval = [m for m in msgs if m["msg_type"] == "APPROVAL"]
    assert len(approval) == 1 and approval[0]["payload"]["version"] == plan["version"]
    assert approval[0]["payload"]["validation"]["status"] == "PASS"
    # labelled fallback: every agent-authored message is rule-based with a reason (never shown as LLM)
    agent_msgs = [m for m in msgs if m["source"] not in ("system", "validator")]
    assert agent_msgs
    for m in agent_msgs:
        assert m["source"] == "rule-based"
        assert (m["fallback_reason"] or "").strip()
    assert all(m["source"] in ("system", "validator", "rule-based") for m in msgs)


def test_baseline_refusal_precedes_acceptance_of_sacrifice(tmp_path):
    """FR-08, FR-14, CONTRACT §5 stance: the sacrificing department refuses first and only accepts later,
    once two return commitments from different agents are on the table."""
    s = svc(tmp_path)
    sc = baseline(s)
    msgs = msgs_of(s, sc["id"])
    plan = s.state()["approved_plan"]
    sac = [d for d, c in plan["modes"].items() if MODE_PACKAGES[c].tier == SACRIFICE][0]
    refusal = next(m for m in msgs if m["sender"] == sac and m["msg_type"] == "REFUSE_SACRIFICE")
    accept = next(m for m in msgs if m["sender"] == sac and m["msg_type"] == "ACCEPT_SACRIFICE")
    assert refusal["round"] < accept["round"]
    offers_before = [m for m in msgs if m["msg_type"] == "COMMITMENT" and m["payload"]["beneficiary"] == sac
                     and m["id"] < accept["id"]]
    assert len({m["payload"]["owner"] for m in offers_before}) >= 2


# ------------------------------------------------------------------------------- 3. judge-edited pools
def test_generous_pool_still_waits_three_rounds(tmp_path):
    """FR-01, FR-12, FR-25: even when all-Standard fits, approval never happens before round 3."""
    s = svc(tmp_path)
    pool = {"power": 200, "water": 200, "oxygen": 200, "robot": 100, "bandwidth": 100}
    sc = baseline(s, pool)
    assert sc["status"] == "APPROVED", sc["result_reason"]
    plan = s.state()["approved_plan"]
    assert approval_round(s, sc["id"]) >= 3 and sc["round"] >= 3
    assert_plan_independently_valid(s, plan, sc)
    assert_fresh_votes(s, plan)
    msgs = msgs_of(s, sc["id"])
    assert all(m["round"] >= 3 for m in msgs if m["msg_type"] in ("VOTE_CALL", "VOTE", "APPROVAL"))


def test_tiny_pool_is_infeasible_with_requested_change(tmp_path):
    """FR-27, FR-15: impossible pool -> INFEASIBLE with blocking constraints and a requested resource change."""
    s = svc(tmp_path)
    sc = baseline(s, {"power": 10, "water": 10, "oxygen": 10, "robot": 5, "bandwidth": 5})
    assert sc["status"] == "INFEASIBLE"
    reason = sc["result_reason"]
    assert "blocking constraints" in reason
    assert "Requested change" in reason
    assert any(r in reason for r in ("Power", "Water", "Oxygen", "Robot", "Bandwidth"))
    assert s.state()["approved_plan"] is None
    assert any(m["msg_type"] == "INFEASIBLE" for m in msgs_of(s, sc["id"]))


@pytest.mark.parametrize("pool", [
    {"power": 79, "water": 52, "oxygen": 59, "robot": 26, "bandwidth": 16},   # bandwidth -1: path B only?
    {"power": 80, "water": 60, "oxygen": 60, "robot": 30, "bandwidth": 18},   # mildly generous
    {"power": 100, "water": 40, "oxygen": 80, "robot": 40, "bandwidth": 20},  # water-starved
    {"power": 78, "water": 52, "oxygen": 59, "robot": 26, "bandwidth": 17},   # power -1
])
def test_edited_pools_never_approve_over_pool(tmp_path, pool):
    """FR-01, FR-22, FR-25, FR-36: for judge-edited pools the run terminates and never approves a plan whose
    totals exceed the pool (independent re-validation)."""
    s = svc(tmp_path)
    sc = baseline(s, pool)
    assert sc["status"] in TERMINAL
    assert sc["result_reason"]
    for p in s.store.plans(sc["id"]):
        if p["status"] == "APPROVED":
            assert_plan_independently_valid(s, p, sc)
            assert_fresh_votes(s, p)
    expected_feasible = bool(optimizer.enumerate_candidates(pool, Policy.baseline()))
    if not expected_feasible:
        assert sc["status"] == "INFEASIBLE"


# ------------------------------------------------------------------------------- 4. practice events
@pytest.mark.parametrize("form", ["json", "text"])
@pytest.mark.parametrize("res,delta,ev_json,ev_text", PRACTICE_EVENTS, ids=[e[0] for e in PRACTICE_EVENTS])
def test_practice_event_recovery(tmp_path, res, delta, ev_json, ev_text, form):
    """FR-28..FR-36, FR-23, FR-24: after an approved baseline, each PDF p8 practice event makes the old plan
    STALE, reviews commitments, uses Crisis Override only after the event, runs >= 2 visible rounds and
    ends APPROVED (fresh 4 votes on a new version) or justified INFEASIBLE, well within 180 s."""
    s = svc(tmp_path)
    base = baseline(s)
    assert base["status"] == "APPROVED"
    old = s.state()["approved_plan"]
    preview = s.preview_event(ev_json if form == "json" else ev_text)
    assert preview["new_pool"][res] == BASELINE_POOL[res] + delta

    t0 = time.monotonic()
    sc = s.inject_event(ev_json if form == "json" else ev_text, "rule-based")
    elapsed = time.monotonic() - t0
    assert elapsed < 60, f"event took {elapsed:.1f}s (budget 180 s)"
    assert sc["kind"] == "EVENT"
    assert sc["pool"][res] == BASELINE_POOL[res] + delta
    assert all(sc["pool"][r] == BASELINE_POOL[r] for r in RESOURCES if r != res)
    assert sc["status"] in ("APPROVED", "INFEASIBLE"), sc["result_reason"]

    st = s.state()
    old_now = s.store.get_plan(old["id"])
    assert old_now["status"] == "STALE"
    assert old_now["note"]
    msgs = msgs_of(s, sc["id"])
    types = [m["msg_type"] for m in msgs]
    assert "EVENT" in types and "PLAN_STALE" in types
    assert types.index("EVENT") < types.index("PLAN_STALE")
    # commitments reviewed: each old accepted return is carried over (PROPOSED again) or VOIDED with a reason
    old_ids = set(old["commitment_ids"])
    carried = [m for m in msgs if m["msg_type"] == "COMMITMENT" and m["round"] == 0]
    voided = [m for m in msgs if m["msg_type"] == "COMMITMENT_VOIDED"]
    assert len(carried) + len(voided) == len(old_ids)
    for m in carried:
        assert any(i in m["content"] for i in old_ids) and "re-accepted" in m["content"]
    for m in voided:
        assert "VOIDED" in m["content"] and "—" in m["content"]
    # Crisis Override never in the baseline scenario
    assert base["policy"]["crisis_override"] is False
    assert all("Override invoked" not in m["content"] for m in msgs_of(s, base["id"]))

    if sc["status"] == "APPROVED":
        plan = st["approved_plan"]
        assert plan["scenario_id"] == sc["id"]
        assert plan["version"] > old["version"]
        assert sc["round"] >= 2
        assert_plan_independently_valid(s, plan, sc)
        assert_fresh_votes(s, plan)
        # votes in the event scenario are all on the new version
        assert approval_round(s, sc["id"]) >= 2
        final_votes = [m for m in msgs if m["msg_type"] == "VOTE"][-4:]
        assert {m["sender"] for m in final_votes} == set(DEPARTMENTS)
        assert all(m["payload"]["plan_version"] == plan["version"] and m["payload"]["decision"] == "ACCEPT"
                   for m in final_votes)
        if plan["policy"]["crisis_override"]:
            assert any("Override invoked" in m["content"] for m in msgs)
        # PRD Appendix A: every practice event is infeasible without Crisis Override
        assert plan["policy"]["crisis_override"] is True
        assert label(plan["modes"]) in {c["label"] for c in optimizer.enumerate_candidates(sc["pool"],
                                                                                           Policy.override())}
    else:
        assert "Requested change" in sc["result_reason"]


def test_official_starter_event_is_infeasible_immediately(tmp_path):
    """FR-27, FR-28, FR-33 (exception), FR-35: the official starter_kit/event_injection_format.json (30% power cut)
    leaves no plan even with Crisis Override -> INFEASIBLE at once with blocking constraints and a requested change."""
    ev = json.loads((ROOT / "starter_kit" / "event_injection_format.json").read_text(encoding="utf-8"))
    s = svc(tmp_path)
    assert baseline(s)["status"] == "APPROVED"
    t0 = time.monotonic()
    sc = s.inject_event(ev, "rule-based")
    assert time.monotonic() - t0 < 30
    assert sc["pool"]["power"] == 79 - 24  # ceil(79 * 0.30) = 24 removed (conservative, D5)
    assert optimizer.enumerate_candidates(sc["pool"], Policy.override()) == []
    assert sc["status"] == "INFEASIBLE"
    assert sc["round"] == 0  # proven infeasible immediately: no negotiation rounds needed
    reason = sc["result_reason"]
    assert "Power" in reason and "Requested change" in reason and "Crisis Override" in reason
    msgs = msgs_of(s, sc["id"])
    assert any(m["msg_type"] == "PLAN_STALE" for m in msgs)
    inf = [m for m in msgs if m["msg_type"] == "INFEASIBLE"]
    assert len(inf) == 1 and inf[0]["payload"]["diagnosis"]["suggestions"]
    assert s.state()["approved_plan"]["status"] == "STALE"


# ------------------------------------------------------------------------------- 5. unseen events
UNSEEN = [
    "Dust storm: power drops by 6 and bandwidth -1",
    {"event_name": "Greenhouse fire", "impact": {"water_reduction": 5}, "forbidden_modes": ["F1"]},
    {"name": "Supply drop", "changes": {"power": 10, "water": 5}},
    {"name": "Quiet sol", "description": "Sensor glitch, no resource impact", "changes": {}},
    "Comms relay restored: bandwidth increases by 3",
    {"event_name": "Oxygen scrubber fault", "impact": {"oxygen_reduction_percent": 5}},
    {"name": "Engineering bay sealed", "changes": {"robot": -2}, "forbidden_modes": ["E1", "E2"]},
]


@pytest.mark.parametrize("event", UNSEEN, ids=["dust_storm_text", "greenhouse_fire_forbidden", "resupply_increase",
                                                "noop", "bandwidth_up_text", "oxygen_percent", "eng_forbidden"])
def test_unseen_events_reach_justified_terminal_state(tmp_path, event):
    """FR-36, FR-27, FR-28, FR-30, FR-32, FR-34: novel events reach APPROVED / INFEASIBLE / DEADLOCK with a clear
    reason; an approved plan always re-validates independently and respects forbidden modes."""
    s = svc(tmp_path)
    assert baseline(s)["status"] == "APPROVED"
    old = s.state()["approved_plan"]
    sc = s.inject_event(event, "rule-based")
    assert sc["status"] in ("APPROVED", "INFEASIBLE", "DEADLOCK"), sc["result_reason"]
    assert sc["result_reason"].strip()
    assert s.store.get_plan(old["id"])["status"] == "STALE"
    forbidden = sc["event"].get("cumulative_forbidden_modes", [])
    for p in s.store.plans(sc["id"]):
        if p["status"] == "APPROVED":
            assert_plan_independently_valid(s, p, sc)
            assert_fresh_votes(s, p)
            assert not set(p["modes"].values()) & set(forbidden)
            assert p["version"] > old["version"]
            assert approval_round(s, sc["id"]) >= 2
            if not p["policy"]["crisis_override"]:
                assert p["policy"]["risk_limit"] == 24
    has_base = bool(optimizer.enumerate_candidates(sc["pool"], Policy.baseline(), forbidden))
    has_over = bool(optimizer.enumerate_candidates(sc["pool"], Policy.override(), forbidden))
    if not (has_base or has_over):
        assert sc["status"] == "INFEASIBLE" and "Requested change" in sc["result_reason"]
    if has_base:  # no reason to escalate risk when the baseline policy suffices
        if sc["status"] == "APPROVED":
            assert s.state()["approved_plan"]["policy"]["crisis_override"] is False


def test_forbidden_mode_event_excludes_mode_everywhere(tmp_path):
    """FR-36, AG-06: a forbidden mode never appears in any drafted plan of the event scenario."""
    s = svc(tmp_path)
    baseline(s)
    sc = s.inject_event({"event_name": "Greenhouse fire", "impact": {"water_reduction": 5},
                         "forbidden_modes": ["F1"]}, "rule-based")
    for p in s.store.plans(sc["id"]):
        assert p["modes"]["FOOD"] != "F1"


# ------------------------------------------------------------------------------- 6. chained events
def test_chained_events_compound_pools(tmp_path):
    """FR-29, FR-30, FR-23, FR-36, NFR-07: two events in a row compound the pool changes, mark the previous
    approved plan STALE each time and keep plan numbering global per session."""
    s = svc(tmp_path)
    baseline(s)
    e1 = s.inject_event({"name": "Solar aftershock", "changes": {"power": -4}}, "rule-based")
    assert e1["pool"]["power"] == 75
    plan1 = s.state()["approved_plan"]
    e2 = s.inject_event("Water contamination: Available Water decreases by 3", "rule-based")
    assert e2["base_pool"] == e1["pool"]
    assert e2["pool"] == {**BASELINE_POOL, "power": 75, "water": 49}
    assert e2["status"] in ("APPROVED", "INFEASIBLE", "DEADLOCK"), e2["result_reason"]
    expected = bool(optimizer.enumerate_candidates(e2["pool"], Policy.override()))
    if not expected:
        assert e2["status"] == "INFEASIBLE"
    if e1["status"] == "APPROVED":
        assert s.store.get_plan(plan1["id"])["status"] == "STALE"
    versions = [p["version"] for p in s.store.session_plans(s.state()["session"]["id"])]
    assert versions == sorted(versions) and len(versions) == len(set(versions))
    if e2["status"] == "APPROVED":
        p = s.state()["approved_plan"]
        assert p["scenario_id"] == e2["id"]
        assert_plan_independently_valid(s, p, e2)
    # a third event after an INFEASIBLE / approved run still works (judge can keep injecting)
    e3 = s.inject_event({"name": "Resupply", "changes": {"power": 8, "water": 6}}, "rule-based")
    assert e3["pool"]["power"] == e2["pool"]["power"] + 8
    assert e3["status"] in ("APPROVED", "INFEASIBLE", "DEADLOCK")


def test_event_after_infeasible_event_keeps_reduced_pool(tmp_path):
    """FR-29, FR-36: an INFEASIBLE event leaves the reduced pool as the new state; a later resupply event
    builds on it and can recover to an approved plan."""
    s = svc(tmp_path)
    baseline(s)
    e1 = s.inject_event({"name": "Blackout", "changes": {"power": -30}}, "rule-based")
    assert e1["status"] == "INFEASIBLE"
    e2 = s.inject_event({"name": "Repair", "changes": {"power": 30}}, "rule-based")
    assert e2["base_pool"]["power"] == 49 and e2["pool"]["power"] == 79
    assert e2["status"] == "APPROVED", e2["result_reason"]
    assert_plan_independently_valid(s, s.state()["approved_plan"], e2)


# ------------------------------------------------------------------------------- 10. exports
def test_export_bundle_files_and_shapes(tmp_path):
    """FR-45, FR-46, FR-48, FR-26, FR-43, NFR-12: export bundle contains the starter files; JSON parses; the CSV
    transcript has one row per message; rule-based messages are flagged in the TXT transcript."""
    s = svc(tmp_path)
    baseline(s)
    s.inject_event({"name": "Solar aftershock", "changes": {"power": -4}}, "rule-based")
    b = s.export_bundle()
    for name in ("final_plan.json", "final_allocation.json", "council_transcript.json", "council_transcript.csv",
                 "council_transcript.txt", "crisis_handling_log.txt", "plans.csv"):
        assert name in b and b[name], name
    fp = json.loads(b["final_plan.json"])
    assert len(fp["scenarios"]) == 2
    for sc in fp["scenarios"]:
        assert sc["final_plan"] is not None
        modes = sc["final_plan"]["modes"]
        assert set(modes) == set(DEPARTMENTS)
        for d, m in modes.items():  # package values exported exactly as published (FR-02, FR-26)
            pkg = MODE_PACKAGES[m["code"]]
            assert all(m[r] == pkg.resources()[r] for r in RESOURCES) and m["risk"] == pkg.risk
    fa = json.loads(b["final_allocation.json"])
    starter_keys = set(json.loads((ROOT / "starter_kit" / "output_schema.json").read_text(encoding="utf-8")))
    assert len(fa["records"]) == 5
    assert {r["agent_id"] for r in fa["records"]} == set(DEPARTMENTS) | {COMMANDER}
    for r in fa["records"]:
        assert starter_keys <= set(r), starter_keys - set(r)
        assert r["flag_human_review"] == (r["risk_score"] is not None and
                                          (fa["records"][-1]["risk_score"] or 0) > 20)
    msgs = json.loads(b["council_transcript.json"])
    assert msgs == json.loads(json.dumps(s.transcript(session_id=s.state()["session"]["id"]), default=str))
    rows = list(csv.DictReader(io.StringIO(b["council_transcript.csv"])))
    assert len(rows) == len(msgs)
    assert [int(r["id"]) for r in rows] == [m["id"] for m in msgs]
    txt = b["council_transcript.txt"]
    n_rule = sum(1 for m in msgs if m["source"] == "rule-based")
    assert n_rule > 0 and txt.count("RULE-BASED FALLBACK") == n_rule
    assert "EVENT" in b["crisis_handling_log.txt"] and "RESULT:" in b["crisis_handling_log.txt"]
    plan_rows = list(csv.reader(io.StringIO(b["plans.csv"])))
    assert len(plan_rows) - 1 == len(s.store.session_plans(s.state()["session"]["id"]))


def test_export_before_any_run_is_safe(tmp_path):
    """FR-45: exporting with no session yields an empty bundle instead of crashing."""
    s = svc(tmp_path)
    assert s.export_bundle() == {}


# ------------------------------------------------------------------------------- 11. PRD Appendix A
def _event_pool(**delta):
    pool = dict(BASELINE_POOL)
    for k, v in delta.items():
        pool[k] += v
    return pool


def test_appendix_a_baseline_has_exactly_two_paths():
    """FR-17, PRD Appendix A: baseline has exactly the two PDF plans."""
    cands = optimizer.enumerate_candidates(dict(BASELINE_POOL), Policy.baseline())
    assert {c["label"] for c in cands} == set(PDF_PATHS)
    assert all(c["template_returns_ok"] for c in cands)


@pytest.mark.parametrize("delta,expected", [
    ({"power": -4}, {"L2+M2+F3+E3", "L3+M2+F2+E3", "L3+M2+F3+E2"}),
    ({"water": -3}, {"L2+M2+F3+E3", "L2+M3+F2+E3", "L3+M2+F2+E3", "L3+M2+F3+E2", "L3+M3+F2+E2"}),
    ({"oxygen": -3}, {"L3+M2+F2+E3", "L3+M3+F2+E2"}),
    ({"robot": -4}, {"L3+M2+F2+E3"}),
    ({"bandwidth": -2}, {"L2+M2+F3+E3", "L2+M3+F2+E3", "L3+M2+F2+E3"}),
])
def test_appendix_a_practice_event_candidate_sets(delta, expected):
    """FR-32, PRD Appendix A: every practice event is infeasible without override; override sets match the PRD."""
    pool = _event_pool(**delta)
    assert optimizer.enumerate_candidates(pool, Policy.baseline()) == []
    assert {c["label"] for c in optimizer.enumerate_candidates(pool, Policy.override())} == expected


@pytest.mark.parametrize("delta", [{"water": -3}, {"oxygen": -3}])
def test_appendix_a_l3_m3_fails_commitment_backing_with_templates(delta):
    """FR-20 (D12), PRD Appendix A: with PDF templates only, L3+M3 combinations fail Commitment backing because both
    need the Commander's exclusive 'first priority next cycle' claim."""
    cands = {c["label"]: c for c in optimizer.enumerate_candidates(_event_pool(**delta), Policy.override())}
    c = cands["L3+M3+F2+E2"]
    assert c["template_returns_ok"] is False
    assert any("commander_next_cycle_first_priority" in i for i in c["template_return_issues"])


def test_appendix_a_e3_water_reserve_claims():
    """FR-20, PRD Appendix A: in Water -3, E3 plans with Water reserve < 2 fail template backing; F3+E3 passes
    (future pledge from a sacrificing owner is a warning only, D13)."""
    cands = {c["label"]: c for c in optimizer.enumerate_candidates(_event_pool(water=-3), Policy.override())}
    assert cands["L3+M2+F2+E3"]["reserve"]["water"] == 0 and not cands["L3+M2+F2+E3"]["template_returns_ok"]
    assert cands["L2+M3+F2+E3"]["reserve"]["water"] == 1 and not cands["L2+M3+F2+E3"]["template_returns_ok"]
    assert cands["L2+M2+F3+E3"]["template_returns_ok"] is True


def test_validator_rejects_l3_m3_claim_conflict_directly():
    """FR-20 (D12): the validator itself (not just the optimizer) fails two ACCEPTED commitments sharing the
    Commander's exclusive claim for different beneficiaries."""
    from ares.models import RETURN_TEMPLATES
    modes = {"LIFE_SUPPORT": "L3", "MEDICAL": "M3", "FOOD": "F2", "ENGINEERING": "E2"}
    comms = []
    for code, ben in (("L3", "LIFE_SUPPORT"), ("M3", "MEDICAL")):
        for i, t in enumerate(RETURN_TEMPLATES[code]):
            comms.append(Commitment(id=f"X-{code}-{i}", scenario_id=1, owner=t.owner, beneficiary=ben, kind=t.kind,
                                    resource=t.resource, amount=t.amount, source=t.source, claim=t.claim,
                                    promise=t.promise, expiry=t.expiry, status="ACCEPTED", template_id=t.template_id))
    v = validate_plan(modes, comms, _event_pool(oxygen=-3), Policy.override())
    assert not v.passed
    assert any(c.name == "Commitment backing" and not c.passed for c in v.checks)


def test_event_parse_of_practice_events_matches_pdf():
    """FR-28: official-style JSON and free text for the practice events parse to the PDF deltas."""
    for res, delta, ev_json, ev_text in PRACTICE_EVENTS:
        for ev in (ev_json, ev_text):
            e = parse_event(ev)
            assert e.deltas.get(res) == delta, (ev, e)
            assert not e.percent_changes and not e.set_values
