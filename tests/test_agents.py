"""Agent tests: rule policy stance, LLM sanitisation and labelled fallback (no network)."""
from __future__ import annotations

import pytest

from ares.agents import CommanderAgent, DepartmentAgent, build_agents
from ares.agents.base import BUDGET_REASON, NO_LLM_REASON
from ares.llm import LLMError
from ares.models import (ACCEPT, ACCEPT_MODE, ACCEPT_SACRIFICE, BASELINE, BASELINE_POOL, COMMANDER, DEPARTMENTS,
                         ENGINEERING, FOOD, LIFE_SUPPORT, MEDICAL, PROPOSAL, REFUSE_SACRIFICE, REJECT, AgentContext,
                         CheckResult, Commitment, Policy, ValidationResult)

POOL = dict(BASELINE_POOL)
CAND_L3 = {"modes": {LIFE_SUPPORT: "L3", MEDICAL: "M2", FOOD: "F2", ENGINEERING: "E2"},
           "totals": {"power": 79, "water": 50, "oxygen": 57, "robot": 26, "bandwidth": 17},
           "reserve": {"power": 0, "water": 2, "oxygen": 2, "robot": 0, "bandwidth": 0}, "risk": 24,
           "sacrifices": [LIFE_SUPPORT], "template_returns_ok": True, "template_return_issues": [],
           "label": "L3+M2+F2+E2", "requires_override": False}
CAND_E3 = {"modes": {LIFE_SUPPORT: "L2", MEDICAL: "M2", FOOD: "F2", ENGINEERING: "E3"},
           "totals": {"power": 79, "water": 50, "oxygen": 59, "robot": 26, "bandwidth": 16},
           "reserve": {"power": 0, "water": 2, "oxygen": 0, "robot": 0, "bandwidth": 1}, "risk": 24,
           "sacrifices": [ENGINEERING], "template_returns_ok": True, "template_return_issues": [],
           "label": "L2+M2+F2+E3", "requires_override": False}


def failing_validation():
    return ValidationResult("FAIL", [CheckResult("Resources", False, "over")],
                            ["Resources: Power 112 > 79 (over by 33); Water 62 > 52 (over by 10)"], [],
                            {"power": 112, "water": 62, "oxygen": 74, "robot": 33, "bandwidth": 26},
                            {"power": -33, "water": -10, "oxygen": -15, "robot": -7, "bandwidth": -9}, 4, [])


def passing_validation(sacrifices=(LIFE_SUPPORT,)):
    return ValidationResult("PASS", [CheckResult("Resources", True, "ok")], [], [], dict(CAND_L3["totals"]),
                            dict(CAND_L3["reserve"]), 24, list(sacrifices))


def commitment(cid, owner, ben, tid, status="PROPOSED", promise="p"):
    return Commitment(id=cid, scenario_id=1, owner=owner, beneficiary=ben, kind="priority", resource=None, amount=0,
                      source=None, claim=None, promise=promise, expiry="End of next planning cycle", status=status,
                      template_id=tid, round_offered=2)


def make_ctx(**kw) -> AgentContext:
    base = dict(scenario_id=1, scenario_kind=BASELINE, round_no=1, max_rounds=6, min_rounds=3, phase="PROPOSAL",
                pool=dict(POOL), policy=Policy.baseline(), override_available=False,
                declared_modes={d: None for d in DEPARTMENTS}, plan=None, validation=None,
                candidates=[CAND_L3, CAND_E3], infeasibility=None, commitments=[], transcript=[],
                sacrifice_requests={})
    base.update(kw)
    return AgentContext(**base)


def negotiation_ctx(round_no=2, first_asked=2, commitments=(), declared=None, **kw):
    declared = declared or {LIFE_SUPPORT: "L1", MEDICAL: "M1", FOOD: "F1", ENGINEERING: "E1"}
    return make_ctx(round_no=round_no, phase="NEGOTIATION", declared_modes=declared,
                    plan={"version": round_no - 1, "modes": dict(declared), "status": "FAILED"},
                    validation=failing_validation(), commitments=list(commitments),
                    commander_requests=dict(CAND_L3["modes"]), sacrifice_requests={LIFE_SUPPORT: first_asked}, **kw)


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def complete_json(self, system, user, timeout_s):
        self.calls += 1
        r = self.replies.pop(0) if self.replies else LLMError("no more replies")
        if isinstance(r, Exception):
            raise r
        if isinstance(r, str):
            from ares.llm import extract_json
            return extract_json(r)
        return r


# ------------------------------------------------------------------ rule policy
def test_proposal_requests_standard_with_consequence():
    for d, std in zip(DEPARTMENTS, ("L1", "M1", "F1", "E1")):
        t = DepartmentAgent(d, None).take_turn(make_ctx())
        assert t.msg_type == PROPOSAL and t.mode == std
        assert "Restricted" in t.consequence and "Sacrifice" in t.consequence
        assert t.source == "rule-based" and t.fallback_reason == NO_LLM_REASON


def test_same_round_sacrifice_request_is_refused_with_counter():
    ctx = negotiation_ctx(commitments=[commitment("S1-C1", COMMANDER, LIFE_SUPPORT, "L3-CMD"),
                                       commitment("S1-C2", ENGINEERING, LIFE_SUPPORT, "L3-ENG")])
    t = DepartmentAgent(LIFE_SUPPORT, None).take_turn(ctx)
    assert t.msg_type == REFUSE_SACRIFICE and t.refuses_sacrifice
    assert t.mode == "L2" and not t.accept_commitment_ids
    assert t.proposed_combo == CAND_E3["modes"]
    assert "L2+M2+F2+E3" in t.message


def test_later_round_two_offers_accepts_sacrifice():
    ctx = negotiation_ctx(round_no=3, first_asked=2,
                          commitments=[commitment("S1-C1", COMMANDER, LIFE_SUPPORT, "L3-CMD"),
                                       commitment("S1-C2", ENGINEERING, LIFE_SUPPORT, "L3-ENG")],
                          declared={LIFE_SUPPORT: "L2", MEDICAL: "M2", FOOD: "F2", ENGINEERING: "E2"})
    t = DepartmentAgent(LIFE_SUPPORT, None).take_turn(ctx)
    assert t.msg_type == ACCEPT_SACRIFICE and t.mode == "L3"
    assert sorted(t.accept_commitment_ids) == ["S1-C1", "S1-C2"]


def test_later_round_single_owner_keeps_refusing():
    ctx = negotiation_ctx(round_no=3, first_asked=2,
                          commitments=[commitment("S1-C1", COMMANDER, LIFE_SUPPORT, "L3-CMD"),
                                       commitment("S1-C3", COMMANDER, LIFE_SUPPORT, None)])
    t = DepartmentAgent(LIFE_SUPPORT, None).take_turn(ctx)
    assert t.msg_type == REFUSE_SACRIFICE and t.mode != "L3"


def test_template_owner_offers_its_template_and_accepts_restricted():
    ctx = negotiation_ctx()
    t = DepartmentAgent(ENGINEERING, None).take_turn(ctx)
    assert [o.template_id for o in t.offers] == ["L3-ENG"]
    assert t.offers[0].beneficiary == LIFE_SUPPORT
    assert t.msg_type == ACCEPT_MODE and t.mode == "E2"
    assert "Power 112 > 79" in t.message
    # not re-offered once on the table
    ctx2 = negotiation_ctx(commitments=[commitment("S1-C2", ENGINEERING, LIFE_SUPPORT, "L3-ENG")])
    assert DepartmentAgent(ENGINEERING, None).take_turn(ctx2).offers == []
    # Medical owns no L3 template
    assert DepartmentAgent(MEDICAL, None).take_turn(ctx).offers == []


def vote_ctx(validation, plan_modes, declared, commitments=()):
    return make_ctx(round_no=3, phase="VOTE", declared_modes=declared, validation=validation,
                    plan={"version": 7, "modes": plan_modes, "status": "VOTING"}, commitments=list(commitments))


def test_vote_logic():
    modes = dict(CAND_L3["modes"])
    accepted = [commitment("S1-C1", COMMANDER, LIFE_SUPPORT, "L3-CMD", "ACCEPTED"),
                commitment("S1-C2", ENGINEERING, LIFE_SUPPORT, "L3-ENG", "ACCEPTED")]
    ok = vote_ctx(passing_validation(), modes, modes, accepted)
    for d in DEPARTMENTS:
        v = DepartmentAgent(d, None).vote(ok)
        assert v.decision == ACCEPT and v.plan_version == 7 and v.source == "rule-based"
    assert DepartmentAgent(MEDICAL, None).vote(vote_ctx(failing_validation(), modes, modes)).decision == REJECT
    mism = vote_ctx(passing_validation(), modes, {**modes, MEDICAL: "M1"}, accepted)
    assert DepartmentAgent(MEDICAL, None).vote(mism).decision == REJECT
    one = vote_ctx(passing_validation(), modes, modes, accepted[:1])
    assert DepartmentAgent(LIFE_SUPPORT, None).vote(one).decision == REJECT


# ------------------------------------------------------------------ LLM path + sanitisation
def test_malformed_json_falls_back_to_rule_based_with_reason():
    p = FakeProvider(["this is not json", "still not json"])
    t = DepartmentAgent(MEDICAL, p).take_turn(make_ctx())
    assert p.calls == 2                                   # one repair retry
    assert t.source == "rule-based" and "invalid JSON" in t.fallback_reason
    assert t.mode == "M1" and t.msg_type == PROPOSAL and t.provider is None


def test_llm_error_falls_back_for_that_turn_only():
    p = FakeProvider([LLMError("groq: HTTP 429 rate limited"),
                      {"msg_type": "PROPOSAL", "mode": "F1", "message": "Food needs F1: W34.", "consequence": "x"}])
    a = DepartmentAgent(FOOD, p)
    t1 = a.take_turn(make_ctx())
    assert t1.source == "rule-based" and "429" in t1.fallback_reason
    t2 = a.take_turn(make_ctx())
    assert t2.source == "llm" and t2.provider == "fake" and t2.model == "fake-1" and t2.fallback_reason is None


def test_other_departments_mode_is_sanitised():
    p = FakeProvider([{"msg_type": "ACCEPT_MODE", "mode": "M2", "message": "ok", "offer_template_ids": ["M3-LS"]}])
    ctx = negotiation_ctx(declared={LIFE_SUPPORT: "L1", MEDICAL: "M1", FOOD: "F1", ENGINEERING: "E1"})
    ctx.commander_requests = {FOOD: "F2"}
    t = DepartmentAgent(FOOD, p).take_turn(ctx)
    assert t.source == "llm" and t.mode == "F1"           # keeps previously declared mode
    assert t.offers == []                                 # cannot offer a template it does not own
    assert "sanitised" in t.reason


def test_llm_same_round_sacrifice_acceptance_is_converted_to_refusal():
    p = FakeProvider([{"msg_type": "ACCEPT_SACRIFICE", "mode": "L3", "message": "fine",
                       "accept_commitment_ids": ["S1-C1", "S1-C2", "BOGUS"]}])
    ctx = negotiation_ctx(commitments=[commitment("S1-C1", COMMANDER, LIFE_SUPPORT, "L3-CMD"),
                                       commitment("S1-C2", ENGINEERING, LIFE_SUPPORT, "L3-ENG")])
    t = DepartmentAgent(LIFE_SUPPORT, p).take_turn(ctx)
    assert t.source == "llm" and t.msg_type == REFUSE_SACRIFICE and t.refuses_sacrifice
    assert t.mode != "L3" and t.accept_commitment_ids == []


def test_llm_later_round_acceptance_filters_ids():
    p = FakeProvider([{"msg_type": "ACCEPT_SACRIFICE", "mode": "L3", "message": "accept",
                       "accept_commitment_ids": ["S1-C1", "S1-C2", "S1-C9"]}])
    ctx = negotiation_ctx(round_no=3, commitments=[commitment("S1-C1", COMMANDER, LIFE_SUPPORT, "L3-CMD"),
                                                   commitment("S1-C2", ENGINEERING, LIFE_SUPPORT, "L3-ENG"),
                                                   commitment("S1-C9", COMMANDER, MEDICAL, "M3-CMD")])
    t = DepartmentAgent(LIFE_SUPPORT, p).take_turn(ctx)
    assert t.msg_type == ACCEPT_SACRIFICE and t.mode == "L3"
    assert sorted(t.accept_commitment_ids) == ["S1-C1", "S1-C2"]


def test_llm_vote_plan_version_forced():
    p = FakeProvider([{"vote": "approve", "reason": "fine", "plan_version": 99}])
    modes = dict(CAND_L3["modes"])
    v = DepartmentAgent(MEDICAL, p).vote(vote_ctx(passing_validation(), modes, modes))
    assert v.decision == ACCEPT and v.plan_version == 7 and v.source == "llm"


def test_time_budget_skips_llm():
    p = FakeProvider([{"msg_type": "PROPOSAL", "mode": "L1", "message": "x"}])
    ctx = make_ctx()
    ctx.time_budget_s = 2.0
    t = DepartmentAgent(LIFE_SUPPORT, p).take_turn(ctx)
    assert p.calls == 0 and t.source == "rule-based" and t.fallback_reason == BUDGET_REASON


# ------------------------------------------------------------------ Commander
def test_commander_rule_picks_candidate_and_offers_template():
    t = CommanderAgent(None).take_turn(negotiation_ctx(round_no=2))
    labels = {c["label"]: c["modes"] for c in (CAND_L3, CAND_E3)}
    assert t.target_combo in labels.values()
    assert t.mode_requests == t.target_combo and t.sacrifice_candidates
    sac = t.sacrifice_candidates[0]
    assert t.offers and all(o.beneficiary == sac for o in t.offers)
    assert not t.use_crisis_override and not t.declare_infeasible
    assert t.source == "rule-based"


def test_commander_switches_after_two_refusals():
    cmd = CommanderAgent(None)
    tr = [{"sender": LIFE_SUPPORT, "msg_type": REFUSE_SACRIFICE, "round": 2, "plan_version": 1,
           "content": "Life Support refuses Sacrifice L3. Counter-proposal: L2+M2+F2+E3."},
          {"sender": LIFE_SUPPORT, "msg_type": REFUSE_SACRIFICE, "round": 3, "plan_version": 2,
           "content": "Life Support refuses again. Counter-proposal: L2+M2+F2+E3."}]
    t = cmd.take_turn(negotiation_ctx(round_no=4, transcript=tr))
    assert t.target_combo == CAND_E3["modes"] and t.sacrifice_candidates == [ENGINEERING]


def test_commander_llm_target_must_be_candidate():
    p = FakeProvider([{"message": "Go all standard", "target_label": "L1+M1+F1+E1",
                       "mode_requests": {LIFE_SUPPORT: "L1"}, "offer_template_ids": ["E3-FOOD"]}])
    t = CommanderAgent(p).take_turn(negotiation_ctx())
    assert t.source == "llm"
    assert t.target_combo in (CAND_L3["modes"], CAND_E3["modes"])
    assert all(o.template_id != "E3-FOOD" for o in t.offers)   # not a Commander template
    assert "sanitised" in t.message


def test_commander_llm_valid_target_kept():
    p = FakeProvider([{"message": "Engineering sacrifices", "target_label": "L2+M2+F2+E3",
                       "offer_template_ids": ["E3-CMD"]}])
    t = CommanderAgent(p).take_turn(negotiation_ctx())
    assert t.target_combo == CAND_E3["modes"] and t.sacrifice_candidates == [ENGINEERING]
    assert [o.template_id for o in t.offers] == ["E3-CMD"]


def test_commander_declares_infeasible_only_without_candidates():
    inf = {"feasible": False, "feasible_with_override": False, "blocking": ["Power: minimum 69 > 60"],
           "nearest": {}, "suggestions": ["+9 Power"]}
    t = CommanderAgent(None).take_turn(negotiation_ctx(candidates=[], infeasibility=inf))
    assert t.declare_infeasible and "+9 Power" in t.message and not t.mode_requests


# ------------------------------------------------------------------ separation + persistence
def test_agents_keep_separate_memory_and_state():
    cmd, depts = build_agents("rule-based")
    assert cmd.agent_id == COMMANDER and set(depts) == set(DEPARTMENTS)
    depts[MEDICAL].take_turn(make_ctx())
    assert len(depts[MEDICAL].memory) == 1
    assert all(len(depts[d].memory) == 0 for d in DEPARTMENTS if d != MEDICAL)
    assert depts[MEDICAL].memory is not depts[FOOD].memory and depts[MEDICAL].state is not depts[FOOD].state
    exported = depts[MEDICAL].export_state()
    fresh = DepartmentAgent(MEDICAL, None)
    fresh.load_state(exported)
    assert fresh.memory == depts[MEDICAL].memory and fresh.state["last_mode"] == "M1"


def test_build_agents_with_missing_key_is_labelled(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    cmd, depts = build_agents("groq")
    t = depts[FOOD].take_turn(make_ctx())
    assert t.source == "rule-based" and "GROQ_API_KEY" in t.fallback_reason
