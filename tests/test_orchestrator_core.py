"""Orchestrator mechanics tested with scripted TEST-ONLY agents (no LLM, no ares.agents).

These fakes exist only to exercise the protocol engine (versioning, vote clearing,
approval gate, event recovery, infeasibility, deadlock). They are never used by the app.
"""
from __future__ import annotations

import json

import pytest

from ares.config import Settings
from ares.models import (
    ACCEPT, ACCEPT_MODE, ACCEPT_SACRIFICE, BASELINE_POOL, COMMANDER, DEPARTMENTS, MODE_PACKAGES, PROPOSAL,
    REFUSE_SACRIFICE, REJECT, RETURN_TEMPLATES, SACRIFICE, CommanderTurn, CommitmentOffer, DepartmentTurn, Vote,
    mode_code,
)
from ares.service import Service
from ares.storage import Store


class FakeCommander:
    agent_id = COMMANDER

    def __init__(self, declare_infeasible=False):
        self.declare_infeasible = declare_infeasible

    def take_turn(self, ctx):
        cands = [c for c in ctx.candidates if c["template_returns_ok"]] or ctx.candidates
        if not cands:
            return CommanderTurn(message="No candidates.", declare_infeasible=True, source="rule-based")
        target = cands[0]
        offers = [CommitmentOffer(beneficiary=t.beneficiary, template_id=t.template_id)
                  for d in target["sacrifices"] for t in RETURN_TEMPLATES[target["modes"][d]] if t.owner == COMMANDER]
        return CommanderTurn(message=f"Target {target['label']}", target_combo=dict(target["modes"]),
                             mode_requests=dict(target["modes"]), sacrifice_candidates=list(target["sacrifices"]),
                             offers=offers, use_crisis_override=target.get("requires_override", False),
                             declare_infeasible=self.declare_infeasible, source="rule-based")

    def export_state(self):
        return {}


class FakeDept:
    def __init__(self, dept, never_sacrifice=False, bad_mode=False):
        self.agent_id = dept
        self.never_sacrifice = never_sacrifice
        self.bad_mode = bad_mode

    def take_turn(self, ctx):
        d = self.agent_id
        if ctx.phase == "PROPOSAL":
            mode = "Z9" if self.bad_mode else mode_code(d, "STANDARD")
            return DepartmentTurn(agent=d, msg_type=PROPOSAL, mode=mode, message="Standard please",
                                  source="rule-based")
        offers = []
        for other, code in ctx.commander_requests.items():
            if other != d and MODE_PACKAGES[code].tier == SACRIFICE:
                offers += [CommitmentOffer(beneficiary=other, template_id=t.template_id)
                           for t in RETURN_TEMPLATES[code] if t.owner == d]
        req = ctx.commander_requests.get(d, ctx.declared_modes.get(d))
        if req and MODE_PACKAGES[req].tier == SACRIFICE:
            mine = [c for c in ctx.commitments if c.beneficiary == d and c.status in ("PROPOSED", "ACCEPTED")]
            if self.never_sacrifice or ctx.sacrifice_requests.get(d) == ctx.round_no or \
                    len({c.owner for c in mine}) < 2:
                keep = ctx.declared_modes.get(d)
                if keep is None or MODE_PACKAGES[keep].tier == SACRIFICE:
                    keep = mode_code(d, "RESTRICTED")
                return DepartmentTurn(agent=d, msg_type=REFUSE_SACRIFICE, mode=keep, message="No.",
                                      refuses_sacrifice=True, offers=offers, source="rule-based")
            return DepartmentTurn(agent=d, msg_type=ACCEPT_SACRIFICE, mode=req, message="Accept with returns",
                                  accept_commitment_ids=[c.id for c in mine if c.status == "PROPOSED"],
                                  offers=offers, source="rule-based")
        return DepartmentTurn(agent=d, msg_type=ACCEPT_MODE, mode=req or mode_code(d, "STANDARD"), message="OK",
                              offers=offers, source="rule-based")

    def vote(self, ctx):
        ok = ctx.validation is not None and ctx.validation.passed
        return Vote(agent=self.agent_id, plan_version=ctx.plan["version"], decision=ACCEPT if ok else REJECT,
                    reason="numbers check out" if ok else "fails", source="rule-based")

    def export_state(self):
        return {"seen": True}


def make_service(tmp_path, **dept_kwargs):
    settings = Settings(db_path=str(tmp_path / "t.db"), default_provider="rule-based")
    factory = lambda provider: (FakeCommander(dept_kwargs.pop("declare_infeasible", False)),
                                {d: FakeDept(d, **dept_kwargs) for d in DEPARTMENTS})
    return Service(settings=settings, store=Store(settings.db_path), agent_factory=factory)


def test_baseline_approves_after_three_rounds(tmp_path):
    svc = make_service(tmp_path)
    s = svc.start_baseline(dict(BASELINE_POOL), "rule-based")
    assert s["status"] == "APPROVED", s["result_reason"]
    assert s["round"] >= 3
    st = svc.state()
    plan = st["plan"]
    assert plan["status"] == "APPROVED"
    valid_votes = [v for v in plan["votes"] if v["valid"]]
    assert len(valid_votes) == 4 and all(v["decision"] == ACCEPT and v["plan_version"] == plan["version"]
                                         for v in valid_votes)
    assert st["protocol"]["sacrifice_refused"] and st["protocol"]["revised_or_rejected"]
    assert st["protocol"]["rounds_before_approval"] >= 3
    labels = "+".join(plan["modes"][d] for d in DEPARTMENTS)
    assert labels in ("L3+M2+F2+E2", "L2+M2+F2+E3")
    assert len(plan["commitment_ids"]) == 2
    types = [m["msg_type"] for m in svc.transcript()]
    for t in ("BRIEFING", "PROPOSAL", "CONFLICT", "REQUEST", "REFUSE_SACRIFICE", "COMMITMENT",
              "COMMITMENT_ACCEPTED", "VOTE_CALL", "VOTE", "APPROVAL"):
        assert t in types, t
    versions = [p["version"] for p in st["plans"]]
    assert versions == sorted(versions) and len(set(versions)) == len(versions)


def test_event_marks_stale_and_recovers_with_override(tmp_path):
    svc = make_service(tmp_path)
    svc.start_baseline(dict(BASELINE_POOL), "rule-based")
    approved_v = svc.state()["plan"]["version"]
    e = svc.inject_event({"name": "Solar aftershock", "changes": {"power": -4}}, "rule-based")
    assert e["status"] == "APPROVED", e["result_reason"]
    assert e["pool"]["power"] == 75
    st = svc.state()
    assert st["plan"]["version"] > approved_v
    assert st["plan"]["policy"]["crisis_override"] is True
    old = [p for p in svc.store.session_plans(st["session"]["id"]) if p["version"] == approved_v][0]
    assert old["status"] == "STALE"
    msgs = svc.transcript(scenario_id=e["id"])
    assert any(m["msg_type"] == "PLAN_STALE" for m in msgs)
    assert sum(1 for m in msgs if m["msg_type"] == "SYSTEM" and m["content"].startswith("Round")) >= 2
    fresh = [v for v in st["plan"]["votes"] if v["valid"]]
    assert len(fresh) == 4 and all(v["plan_version"] == st["plan"]["version"] for v in fresh)


def test_event_infeasible_immediately_with_suggestion(tmp_path):
    svc = make_service(tmp_path)
    svc.start_baseline(dict(BASELINE_POOL), "rule-based")
    e = svc.inject_event({"name": "Rover fleet lost", "changes": {"robot": -20}}, "rule-based")
    assert e["status"] == "INFEASIBLE"
    assert "Requested change" in e["result_reason"]
    assert e["round"] == 0


def test_deadlock_when_nobody_sacrifices(tmp_path):
    svc = make_service(tmp_path, never_sacrifice=True)
    s = svc.start_baseline(dict(BASELINE_POOL), "rule-based", max_rounds=4)
    assert s["status"] == "DEADLOCK"
    assert all(p["status"] != "APPROVED" for p in svc.state()["plans"])


def test_commander_cannot_declare_infeasible_when_plans_exist(tmp_path):
    svc = make_service(tmp_path, declare_infeasible=True)
    s = svc.start_baseline(dict(BASELINE_POOL), "rule-based")
    assert s["status"] == "APPROVED"
    assert any("infeasibility claim rejected" in m["content"] for m in svc.transcript())


def test_invalid_mode_is_ignored(tmp_path):
    svc = make_service(tmp_path, bad_mode=True)
    svc.start_baseline(dict(BASELINE_POOL), "rule-based")
    assert any("invalid mode 'Z9' ignored" in m["content"] for m in svc.transcript())


def test_plan_change_clears_votes_and_exports(tmp_path):
    svc = make_service(tmp_path)
    svc.start_baseline(dict(BASELINE_POOL), "rule-based")
    svc.inject_event({"name": "Solar aftershock", "changes": {"power": -4}}, "rule-based")
    bundle = svc.export_bundle()
    for name in ("final_plan.json", "final_allocation.json", "council_transcript.json", "council_transcript.csv",
                 "council_transcript.txt", "crisis_handling_log.txt", "plans.csv"):
        assert bundle[name], name
    alloc = json.loads(bundle["final_allocation.json"])
    assert len(alloc["records"]) == 5 and alloc["records"][0]["vote"] in ("APPROVE", "REJECT")
    assert "Solar aftershock" in bundle["crisis_handling_log.txt"]


def test_persistence_across_sessions(tmp_path):
    svc = make_service(tmp_path)
    svc.start_baseline(dict(BASELINE_POOL), "rule-based")
    svc.reset_session()
    svc.start_baseline(dict(BASELINE_POOL), "rule-based")
    sessions = {m["session_id"] for m in svc.transcript()}
    assert len(sessions) == 2


def test_baseline_tiny_pool_infeasible(tmp_path):
    svc = make_service(tmp_path)
    s = svc.start_baseline({"power": 10, "water": 10, "oxygen": 10, "robot": 5, "bandwidth": 5}, "rule-based")
    assert s["status"] == "INFEASIBLE"


def test_cosign_required_holds_approval_until_judge_signs(tmp_path):
    svc = make_service(tmp_path)
    s = svc.start_baseline(dict(BASELINE_POOL), "rule-based", require_cosign=True)
    assert s["status"] == "AWAITING_COSIGN"  # both baseline plans have risk 24 > 20
    assert svc.state()["plan"]["status"] == "AWAITING_COSIGN"
    with pytest.raises(ValueError):
        svc.inject_event({"name": "x", "changes": {"power": -4}}, "rule-based")
    done = svc.cosign(True, "looks safe")
    assert done["status"] == "APPROVED"
    assert svc.state()["plan"]["status"] == "APPROVED"
    assert any(m["msg_type"] == "APPROVAL" and "co-signed" in m["content"] for m in svc.transcript())
    with pytest.raises(ValueError):
        svc.cosign(True)


def test_cosign_veto_blocks_plan(tmp_path):
    svc = make_service(tmp_path)
    svc.start_baseline(dict(BASELINE_POOL), "rule-based", require_cosign=True)
    vetoed = svc.cosign(False, "too risky")
    assert vetoed["status"] == "VETOED"
    assert svc.state()["plan"]["status"] == "REJECTED"
    assert svc.store.latest_approved_plan(svc.state()["session"]["id"]) is None
