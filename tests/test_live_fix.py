"""Regression tests for the live-Gemini deadlock (2026-10-10).

Real failure: plan v4 L2+M2+F2+E3 passed the validator with 2 ACCEPTED returns for Engineering, but the three
Restricted departments voted REJECT claiming "we are sacrificing without 2 accepted return commitments", because
the vote prompt never told them their tier, who was sacrificing, or whether compensation was met.
"""
from __future__ import annotations

import pytest

from ares import factcheck, validator
from ares.agents import DepartmentAgent, prompts
from ares.config import Settings
from ares.llm.base import LLMError
from ares.models import (
    ACCEPT, ACCEPT_MODE, ACCEPT_SACRIFICE, BASELINE_POOL, COMMANDER, DEPARTMENTS, ENGINEERING, FOOD, LIFE_SUPPORT,
    MEDICAL, MODE_PACKAGES, PROPOSAL, REFUSE_SACRIFICE, REJECT, RETURN_TEMPLATES, SACRIFICE, AgentContext,
    CommanderTurn, Commitment, CommitmentOffer, DepartmentTurn, Policy, Vote, mode_code,
)
from ares.optimizer import enumerate_candidates
from ares.service import Service
from ares.storage import Store

PLAN_B = {LIFE_SUPPORT: "L2", MEDICAL: "M2", FOOD: "F2", ENGINEERING: "E3"}
FALSE_REASON = "We are making sacrifices without receiving the required 2 accepted return commitments from different agents."


def _comm(cid, owner, status, tid):
    t = next(t for t in RETURN_TEMPLATES["E3"] if t.template_id == tid)
    return Commitment(id=cid, scenario_id=1, owner=owner, beneficiary=ENGINEERING, kind=t.kind, resource=t.resource,
                      amount=t.amount, source=t.source, claim=t.claim, promise=t.promise, expiry=t.expiry,
                      status=status, template_id=tid)


def _plan_b_state(statuses=("ACCEPTED", "ACCEPTED")):
    comms = [_comm("S1-C2", COMMANDER, statuses[0], "E3-CMD"), _comm("S1-C3", FOOD, statuses[1], "E3-FOOD")]
    v = validator.validate_plan(PLAN_B, comms, dict(BASELINE_POOL), Policy.baseline())
    plan = {"version": 4, "status": "VOTING", "modes": dict(PLAN_B), "commitment_ids": ["S1-C2", "S1-C3"],
            "scenario_id": 1}
    return plan, v, comms


def _ctx(plan, v, comms, phase="VOTE"):
    return AgentContext(scenario_id=1, scenario_kind="BASELINE", round_no=4, max_rounds=6, min_rounds=3, phase=phase,
                        pool=dict(BASELINE_POOL), policy=Policy.baseline(), override_available=False,
                        declared_modes=dict(PLAN_B), plan=plan, validation=v, candidates=[], infeasibility=None,
                        commitments=comms, transcript=[], sacrifice_requests={ENGINEERING: 3})


# ----------------------------------------------------------------------------- snapshot + vote context
def test_snapshot_reports_the_real_plan_state():
    plan, v, comms = _plan_b_state()
    assert v.passed
    snap = factcheck.plan_snapshot(plan, v, comms, dict(BASELINE_POOL), dict(PLAN_B))
    assert snap["sacrificing"] == [ENGINEERING]
    assert snap["compensation"][ENGINEERING]["met"] is True
    assert snap["compensation"][ENGINEERING]["owners"] == [COMMANDER, FOOD]
    text = factcheck.snapshot_text(snap, LIFE_SUPPORT)
    assert "SACRIFICING DEPARTMENTS (Sacrifice-tier mode): ENGINEERING (E3)" in text
    assert "You are NOT in Sacrifice mode" in text
    assert "ACCEPTED (counts): S1-C2 COMMANDER -> ENGINEERING" in text
    assert "ACCEPTED (counts): S1-C3 FOOD -> ENGINEERING" in text
    assert "-> MET" in text
    for name in ("Mode packages", "Resources", "Risk", "Sacrifice limit", "Return agreement", "Commitment backing",
                 "Policy"):
        assert f"[PASS] {name}" in text


def test_offer_is_never_counted_as_accepted():
    plan, v, comms = _plan_b_state(("ACCEPTED", "PROPOSED"))
    assert not v.passed  # validator agrees: only 1 accepted
    snap = factcheck.plan_snapshot(plan, v, comms, dict(BASELINE_POOL), dict(PLAN_B))
    comp = snap["compensation"][ENGINEERING]
    assert [c.id for c in comp["accepted"]] == ["S1-C2"] and [c.id for c in comp["offered"]] == ["S1-C3"]
    assert comp["met"] is False
    text = factcheck.snapshot_text(snap, ENGINEERING)
    assert "OFFERED, NOT ACCEPTED (does not count): S1-C3" in text and "NOT MET" in text
    assert "You ARE a sacrificing department" in text


def test_vote_prompt_contains_verified_facts_for_every_department():
    plan, v, comms = _plan_b_state()
    ctx = _ctx(plan, v, comms)
    for d in DEPARTMENTS:
        system, user = prompts.vote_prompt(DepartmentAgent(d, None), ctx)
        assert "Restricted is NOT Sacrifice" in system
        assert "VERIFIED PLAN FACTS" in user and "PLAN UNDER VOTE: v4" in user
        assert "COMPENSATION REQUIREMENT for ENGINEERING: 2 accepted from 2 different agent(s)" in user
        assert ("You ARE a sacrificing department" in user) == (d == ENGINEERING)


# ----------------------------------------------------------------------------- contradiction detection
@pytest.mark.parametrize("voter,reason", [
    (LIFE_SUPPORT, FALSE_REASON),
    (MEDICAL, "The plan requires sacrifices with zero accepted return commitments from different agents."),
    (FOOD, "We are sacrificing water use without having 2 accepted return commitments from different agents."),
])
def test_false_reasons_from_the_live_run_are_detected(voter, reason):
    plan, v, comms = _plan_b_state()
    snap = factcheck.plan_snapshot(plan, v, comms, dict(BASELINE_POOL), dict(PLAN_B))
    corrections = factcheck.find_contradictions(voter, reason, snap)
    assert corrections, reason
    assert any("2 ACCEPTED return commitments" in c for c in corrections)


@pytest.mark.parametrize("reason", [
    "Our oxygen margin under L2 is too thin for 42 colonists; we object to this allocation.",
    "The return agreement for Engineering is unfair to Food because water priority should stay with crops.",
])
def test_genuine_objections_are_not_flagged(reason):
    plan, v, comms = _plan_b_state()
    snap = factcheck.plan_snapshot(plan, v, comms, dict(BASELINE_POOL), dict(PLAN_B))
    assert factcheck.find_contradictions(LIFE_SUPPORT, reason, snap) == []


def test_true_missing_returns_claim_is_not_contradicted():
    plan, v, comms = _plan_b_state(("ACCEPTED", "PROPOSED"))
    snap = factcheck.plan_snapshot(plan, v, comms, dict(BASELINE_POOL), dict(PLAN_B))
    assert factcheck.find_contradictions(ENGINEERING, "We sacrifice E3 with only 1 accepted return commitment.",
                                         snap) == []


# ----------------------------------------------------------------------------- orchestrator behaviour
class TargetCommander:
    """TEST-ONLY scripted Commander: prefers candidates where `prefer` sacrifices, offers its own templates."""
    agent_id = COMMANDER

    def __init__(self, prefer=ENGINEERING):
        self.prefer = prefer

    def take_turn(self, ctx):
        cands = sorted(ctx.candidates, key=lambda c: (self.prefer not in c["sacrifices"], not c["template_returns_ok"],
                                                      c["risk"], c["label"]))
        if not cands:
            return CommanderTurn(message="none", declare_infeasible=True, source="rule-based")
        t = cands[0]
        offers = [CommitmentOffer(beneficiary=x.beneficiary, template_id=x.template_id)
                  for d in t["sacrifices"] for x in RETURN_TEMPLATES[t["modes"][d]] if x.owner == COMMANDER]
        return CommanderTurn(message=f"Target {t['label']}", target_combo=dict(t["modes"]),
                             mode_requests=dict(t["modes"]), sacrifice_candidates=list(t["sacrifices"]),
                             offers=offers, use_crisis_override=t.get("requires_override", False), source="rule-based")

    def export_state(self):
        return {}


class ConfusedDept(DepartmentAgent):
    """Real rule-based department whose FIRST vote repeats the live false reason (if not sacrificing)."""

    def __init__(self, dept, mode="confused"):
        super().__init__(dept, None)
        self.mode = mode
        self.reconsider_calls = 0

    def vote(self, ctx):
        mine = (ctx.plan or {}).get("modes", {}).get(self.agent_id)
        if MODE_PACKAGES[mine].tier != SACRIFICE:
            if self.mode == "genuine":
                return Vote(self.agent_id, ctx.plan["version"], REJECT,
                            "Our oxygen margin is too thin for 42 colonists; we object.", source="rule-based")
            return Vote(self.agent_id, ctx.plan["version"], REJECT, FALSE_REASON, source="rule-based")
        return super().vote(ctx)

    def reconsider_vote(self, ctx, original, corrections):
        self.reconsider_calls += 1
        if self.mode == "stubborn":
            return Vote(self.agent_id, ctx.plan["version"], REJECT, "We still object after the fact check.",
                        source="rule-based", reconsidered=True)
        return super().reconsider_vote(ctx, original, corrections)


def _svc(tmp_path, dept_factory):
    s = Settings(db_path=str(tmp_path / "t.db"), default_provider="rule-based")
    depts_holder = {}

    def factory(provider):
        depts = {d: dept_factory(d) for d in DEPARTMENTS}
        depts_holder["d"] = depts
        return TargetCommander(), depts
    return Service(settings=s, store=Store(s.db_path), agent_factory=factory), depts_holder


def test_false_rejection_gets_recorded_fact_check_and_genuine_reconsideration(tmp_path):
    svc, holder = _svc(tmp_path, lambda d: ConfusedDept(d))
    s = svc.start_baseline(dict(BASELINE_POOL), "rule-based")
    assert s["status"] == "APPROVED", s["result_reason"]
    msgs = svc.transcript(scenario_id=s["id"])
    checks = [m for m in msgs if m["msg_type"] == "FACT_CHECK"]
    assert len(checks) == 3 and {m["recipient"] for m in checks} == {LIFE_SUPPORT, MEDICAL, FOOD}
    votes = [m for m in msgs if m["msg_type"] == "VOTE"]
    originals = [m for m in votes if not m["payload"].get("reconsidered")]
    recon = [m for m in votes if m["payload"].get("reconsidered")]
    assert sum(1 for m in originals if m["content"].startswith("REJECT")) == 3      # original objections preserved
    assert len(recon) == 3 and all("reconsidered after fact check" in m["content"] for m in recon)
    plan = svc.state()["plan"]
    valid = [v for v in plan["votes"] if v["valid"]]
    assert len(valid) == 4 and all(v["decision"] == ACCEPT and v["plan_version"] == plan["version"] for v in valid)
    assert sum(1 for v in plan["votes"] if not v["valid"] and v["decision"] == REJECT) == 3   # superseded, kept
    assert svc.state()["protocol"]["return_commitments"] == 2


def test_genuine_disagreement_is_preserved_and_blocks_approval(tmp_path):
    svc, holder = _svc(tmp_path, lambda d: ConfusedDept(d, mode="genuine"))
    s = svc.start_baseline(dict(BASELINE_POOL), "rule-based", max_rounds=4)
    assert s["status"] == "DEADLOCK"
    msgs = svc.transcript(scenario_id=s["id"])
    assert not any(m["msg_type"] == "FACT_CHECK" for m in msgs)
    assert all(d.reconsider_calls == 0 for d in holder["d"].values())
    assert all(p["status"] != "APPROVED" for p in svc.state()["plans"])
    # protocol counts the accepted returns of the last plan even without approval
    assert svc.state()["protocol"]["return_commitments"] == 2


def test_reconsidered_reject_stays_reject(tmp_path):
    svc, _ = _svc(tmp_path, lambda d: ConfusedDept(d, mode="stubborn"))
    s = svc.start_baseline(dict(BASELINE_POOL), "rule-based", max_rounds=3)
    assert s["status"] == "DEADLOCK"
    plan = svc.state()["plan"]
    assert plan["status"] == "REJECTED"
    valid = [v for v in plan["votes"] if v["valid"]]
    assert sum(1 for v in valid if v["decision"] == REJECT) == 3


def test_commander_request_names_every_department(tmp_path):
    svc, _ = _svc(tmp_path, lambda d: DepartmentAgent(d, None))
    s = svc.start_baseline(dict(BASELINE_POOL), "rule-based")
    reqs = [m for m in svc.transcript(scenario_id=s["id"]) if m["msg_type"] == "REQUEST"]
    assert reqs and all(set(m["payload"]["mode_requests"]) == set(DEPARTMENTS) for m in reqs)


def test_post_event_recovery_keeps_compensated_sacrifice_and_needs_fresh_votes(tmp_path):
    svc, _ = _svc(tmp_path, lambda d: DepartmentAgent(d, None))
    b = svc.start_baseline(dict(BASELINE_POOL), "rule-based")
    assert b["status"] == "APPROVED"
    old = svc.state()["plan"]
    assert old["modes"] == PLAN_B
    e = svc.inject_event({"name": "Solar aftershock", "changes": {"power": -4}}, "rule-based")
    assert e["status"] == "APPROVED", e["result_reason"]
    st = svc.state()
    new = st["plan"]
    assert new["version"] > old["version"] and new["policy"]["crisis_override"] is True
    stale = [p for p in svc.store.session_plans(st["session"]["id"]) if p["version"] == old["version"]][0]
    assert stale["status"] == "STALE" and not svc.store.votes(stale["id"], valid_only=True)
    msgs = svc.transcript(scenario_id=e["id"])
    assert not any(m["sender"] == ENGINEERING and m["msg_type"] == REFUSE_SACRIFICE for m in msgs)  # continuing
    comms = svc.store.commitments(e["id"])
    v = validator.validate_plan(new["modes"], comms, e["pool"], Policy.override())
    assert v.passed
    for d in v.sacrifices:
        owners = {c.owner for c in comms if c.beneficiary == d and c.status == "ACCEPTED"}
        assert len(owners) >= 2, d
    fresh = [x for x in new["votes"] if x["valid"]]
    assert len(fresh) == 4 and all(x["decision"] == ACCEPT and x["plan_version"] == new["version"] for x in fresh)


def test_oracle_post_event_candidate_totals():
    """User-supplied oracle (correct label is L3+M2+F2+E3; L2+M2+F2+E3 needs Power 79 > 75)."""
    pool = {**BASELINE_POOL, "power": 75}
    cands = {c["label"]: c for c in enumerate_candidates(pool, Policy.override())}
    c = cands["L3+M2+F2+E3"]
    assert c["totals"] == {"power": 75, "water": 49, "oxygen": 56, "robot": 22, "bandwidth": 15} and c["risk"] == 28
    assert "L2+M2+F2+E3" not in cands


# ----------------------------------------------------------------------------- provider labels + failures
class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, reply=None, exc=None):
        self.reply, self.exc = reply, exc

    def complete_json(self, system, user, timeout_s):
        if self.exc:
            raise self.exc
        return self.reply


def test_llm_votes_carry_provider_and_model():
    plan, v, comms = _plan_b_state()
    vote = DepartmentAgent(MEDICAL, FakeProvider({"vote": "ACCEPT", "reason": "PASS; M2 as declared."})).vote(
        _ctx(plan, v, comms))
    assert (vote.source, vote.provider, vote.model) == ("llm", "fake", "fake-1")


def test_reconsider_falls_back_on_api_failure_with_label():
    plan, v, comms = _plan_b_state()
    agent = DepartmentAgent(LIFE_SUPPORT, FakeProvider(exc=LLMError("HTTP 503 unavailable")))
    original = Vote(LIFE_SUPPORT, 4, REJECT, FALSE_REASON)
    new = agent.reconsider_vote(_ctx(plan, v, comms), original, ["fact"])
    assert new.reconsidered and new.source == "rule-based" and "503" in (new.fallback_reason or "")
    assert new.decision == ACCEPT  # rule policy evaluates the verified facts


def test_reconsider_with_malformed_json_falls_back():
    plan, v, comms = _plan_b_state()
    agent = DepartmentAgent(FOOD, FakeProvider(reply=["not", "a", "dict"]))
    new = agent.reconsider_vote(_ctx(plan, v, comms), Vote(FOOD, 4, REJECT, FALSE_REASON), ["fact"])
    assert new.source == "rule-based" and new.reconsidered
