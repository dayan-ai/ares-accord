"""Shared data contract for ARES Accord.

OWNER: orchestrator (main session). Other modules import from here and must NOT
redefine these constants or shapes. Changes to this file are contract changes.

All values in MODE_PACKAGES are copied verbatim from the participant guide
(AI ML - Virtual.pdf, v2.1, page 4) and are immutable at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from types import MappingProxyType
from typing import Any, Mapping

# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------
RESOURCES: tuple[str, ...] = ("power", "water", "oxygen", "robot", "bandwidth")
RESOURCE_LABELS: Mapping[str, str] = MappingProxyType({
    "power": "Power", "water": "Water", "oxygen": "Oxygen",
    "robot": "Robot time", "bandwidth": "Bandwidth",
})

COMMANDER = "COMMANDER"
LIFE_SUPPORT = "LIFE_SUPPORT"
MEDICAL = "MEDICAL"
FOOD = "FOOD"
ENGINEERING = "ENGINEERING"
DEPARTMENTS: tuple[str, ...] = (LIFE_SUPPORT, MEDICAL, FOOD, ENGINEERING)
AGENTS: tuple[str, ...] = (COMMANDER,) + DEPARTMENTS
AGENT_LABELS: Mapping[str, str] = MappingProxyType({
    COMMANDER: "Commander", LIFE_SUPPORT: "Life Support", MEDICAL: "Medical",
    FOOD: "Food Production", ENGINEERING: "Engineering",
    "SYSTEM": "System", "VALIDATOR": "Validator", "JUDGE": "Judge",
})
DEPT_PREFIX: Mapping[str, str] = MappingProxyType({
    LIFE_SUPPORT: "L", MEDICAL: "M", FOOD: "F", ENGINEERING: "E",
})

STANDARD, RESTRICTED, SACRIFICE = "STANDARD", "RESTRICTED", "SACRIFICE"
TIERS = (STANDARD, RESTRICTED, SACRIFICE)

BASELINE_POOL: Mapping[str, int] = MappingProxyType({
    "power": 79, "water": 52, "oxygen": 59, "robot": 26, "bandwidth": 17,
})

# Scenario kinds
BASELINE = "BASELINE"
EVENT = "EVENT"


# ---------------------------------------------------------------------------
# Mode packages (immutable)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ModePackage:
    code: str            # e.g. "L1"
    department: str      # e.g. LIFE_SUPPORT
    tier: str            # STANDARD | RESTRICTED | SACRIFICE
    power: int
    water: int
    oxygen: int
    robot: int
    bandwidth: int
    risk: int
    consequence: str

    def resources(self) -> dict[str, int]:
        return {r: getattr(self, r) for r in RESOURCES}

    @property
    def label(self) -> str:
        return f"{self.code} {self.tier.title()}"


_RESTRICTED_TEXT = "Completes the immediate task but creates recovery debt."
_STANDARD_TEXT = "Protects the full mission."

MODE_PACKAGES: Mapping[str, ModePackage] = MappingProxyType({
    p.code: p for p in (
        ModePackage("L1", LIFE_SUPPORT, STANDARD, 34, 10, 42, 4, 4, 1, _STANDARD_TEXT),
        ModePackage("L2", LIFE_SUPPORT, RESTRICTED, 27, 9, 36, 6, 3, 5, _RESTRICTED_TEXT),
        ModePackage("L3", LIFE_SUPPORT, SACRIFICE, 23, 8, 33, 2, 2, 9,
                    "Living zone closes; crew crowding and thermal limits continue for 48 hours."),
        ModePackage("M1", MEDICAL, STANDARD, 28, 12, 18, 3, 10, 1, _STANDARD_TEXT),
        ModePackage("M2", MEDICAL, RESTRICTED, 21, 10, 14, 2, 6, 5, _RESTRICTED_TEXT),
        ModePackage("M3", MEDICAL, SACRIFICE, 18, 8, 12, 2, 5, 9,
                    "One surgery is delayed and the patient remains unstable."),
        ModePackage("F1", FOOD, STANDARD, 24, 34, 8, 4, 2, 1, _STANDARD_TEXT),
        ModePackage("F2", FOOD, RESTRICTED, 15, 27, 5, 4, 2, 5, _RESTRICTED_TEXT),
        ModePackage("F3", FOOD, SACRIFICE, 10, 24, 5, 2, 1, 9,
                    "One crop bay is abandoned; food reserve falls by 40 percent."),
        ModePackage("E1", ENGINEERING, STANDARD, 26, 6, 6, 22, 10, 1, _STANDARD_TEXT),
        ModePackage("E2", ENGINEERING, RESTRICTED, 20, 5, 5, 18, 7, 5, _RESTRICTED_TEXT),
        ModePackage("E3", ENGINEERING, SACRIFICE, 16, 4, 4, 14, 5, 9,
                    "The rover is cannibalized; future repair capacity is reduced."),
    )
})


def modes_for(department: str) -> list[ModePackage]:
    return [p for p in MODE_PACKAGES.values() if p.department == department]


def mode_code(department: str, tier: str) -> str:
    return f"{DEPT_PREFIX[department]}{TIERS.index(tier) + 1}"


# ---------------------------------------------------------------------------
# Policy (risk limit / sacrifice limit). Crisis Override only after an event.
# ---------------------------------------------------------------------------
BASELINE_RISK_LIMIT = 24
BASELINE_MAX_SACRIFICES = 1
OVERRIDE_RISK_LIMIT = 28
OVERRIDE_MAX_SACRIFICES = 2


@dataclass(frozen=True)
class Policy:
    risk_limit: int = BASELINE_RISK_LIMIT
    max_sacrifices: int = BASELINE_MAX_SACRIFICES
    crisis_override: bool = False

    @staticmethod
    def baseline() -> "Policy":
        return Policy()

    @staticmethod
    def override() -> "Policy":
        return Policy(OVERRIDE_RISK_LIMIT, OVERRIDE_MAX_SACRIFICES, True)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Commitments (return agreements)
# ---------------------------------------------------------------------------
# kind:   "resource" -> a quantity of a resource; "priority" -> a future claim
# source: "package"  -> carved out of the owner's own selected package
#         "reserve"  -> taken from the unallocated colony reserve (Commander only)
#         "future"   -> pledged from a future cycle (not counted against this pool)
#         None       -> priority commitments
# claim:  exclusive future-claim key; two ACCEPTED commitments with the same claim
#         key for different beneficiaries conflict.
# status: PROPOSED | ACCEPTED | REJECTED | VOIDED
COMMITMENT_STATUSES = ("PROPOSED", "ACCEPTED", "REJECTED", "VOIDED")
DEFAULT_EXPIRY = "End of next planning cycle"


@dataclass(frozen=True)
class CommitmentTemplate:
    template_id: str
    sacrifice_mode: str
    owner: str
    beneficiary: str
    kind: str
    resource: str | None
    amount: int
    source: str | None
    claim: str | None
    promise: str
    expiry: str = DEFAULT_EXPIRY


# PDF page 6, "Required return agreement"
RETURN_TEMPLATES: Mapping[str, tuple[CommitmentTemplate, ...]] = MappingProxyType({
    "L3": (
        CommitmentTemplate("L3-ENG", "L3", ENGINEERING, LIFE_SUPPORT, "resource", "robot", 4, "package", None,
                           "Engineering commits 4 of its Robot units to habitat inspection."),
        CommitmentTemplate("L3-CMD", "L3", COMMANDER, LIFE_SUPPORT, "priority", None, 0, None,
                           "commander_next_cycle_first_priority",
                           "Commander gives habitat restoration first priority next cycle."),
    ),
    "M3": (
        CommitmentTemplate("M3-LS", "M3", LIFE_SUPPORT, MEDICAL, "resource", "oxygen", 3, "package", None,
                           "Life Support reserves 3 of its Oxygen units for care."),
        CommitmentTemplate("M3-CMD", "M3", COMMANDER, MEDICAL, "priority", None, 0, None,
                           "commander_next_cycle_first_priority",
                           "Commander gives Medical first priority next cycle."),
    ),
    "F3": (
        CommitmentTemplate("F3-ENG", "F3", ENGINEERING, FOOD, "resource", "robot", 4, "future", None,
                           "Engineering commits 4 future Robot units to greenhouse rebuild."),
        CommitmentTemplate("F3-CMD", "F3", COMMANDER, FOOD, "priority", None, 0, None,
                           "next_water_increase",
                           "Commander protects the next Water increase for Food."),
    ),
    "E3": (
        CommitmentTemplate("E3-CMD", "E3", COMMANDER, ENGINEERING, "resource", "water", 2, "reserve", None,
                           "Commander assigns the 2-unit Water reserve to rover cooling."),
        CommitmentTemplate("E3-FOOD", "E3", FOOD, ENGINEERING, "priority", None, 0, None,
                           "next_recovered_water",
                           "Food gives Engineering first priority on the next recovered Water."),
    ),
})


@dataclass
class Commitment:
    id: str                    # e.g. "S1-C3" (scenario 1, commitment 3)
    scenario_id: int
    owner: str                 # agent id (COMMANDER or a department)
    beneficiary: str           # the sacrificing department
    kind: str                  # "resource" | "priority"
    resource: str | None
    amount: int
    source: str | None         # "package" | "reserve" | "future" | None
    claim: str | None
    promise: str
    expiry: str
    status: str = "PROPOSED"
    template_id: str | None = None
    round_offered: int = 0
    round_resolved: int | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "Commitment":
        keys = Commitment.__dataclass_fields__.keys()
        return Commitment(**{k: d[k] for k in keys if k in d})


@dataclass
class CommitmentOffer:
    """What an agent emits when it offers a return commitment.

    The orchestrator turns an offer into a Commitment (assigning id, owner,
    status=PROPOSED). If template_id is set, fields are filled from the template
    and the offer's own fields are ignored.
    """
    beneficiary: str
    template_id: str | None = None
    kind: str = "priority"
    resource: str | None = None
    amount: int = 0
    source: str | None = None
    claim: str | None = None
    promise: str = ""
    expiry: str = DEFAULT_EXPIRY


# ---------------------------------------------------------------------------
# Validation results
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass
class ValidationResult:
    status: str                         # "PASS" | "FAIL"
    checks: list[CheckResult]
    reasons: list[str]                  # human-readable failure reasons (empty on PASS)
    warnings: list[str]
    totals: dict[str, int]              # summed resources of the selected packages
    reserve: dict[str, int]             # pool - totals (can be negative)
    risk: int
    sacrifices: list[str]               # departments in Sacrifice mode

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Votes
# ---------------------------------------------------------------------------
ACCEPT, REJECT = "ACCEPT", "REJECT"


@dataclass
class Vote:
    agent: str
    plan_version: int
    decision: str          # ACCEPT | REJECT
    reason: str
    source: str = "llm"    # "llm" | "rule-based"
    fallback_reason: str | None = None
    provider: str | None = None
    model: str | None = None
    reconsidered: bool = False          # True = second vote after a recorded fact-check

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Crisis events (canonical internal form; see ares/events.py for parsing)
# ---------------------------------------------------------------------------
@dataclass
class CrisisEvent:
    event_id: str
    name: str
    description: str
    deltas: dict[str, int] = field(default_factory=dict)          # absolute change, negative = loss
    percent_changes: dict[str, float] = field(default_factory=dict)  # e.g. {"power": -30.0}
    set_values: dict[str, int] = field(default_factory=dict)      # absolute new availability
    forbidden_modes: list[str] = field(default_factory=list)      # mode codes no longer usable
    requires_replan: bool = True
    message_to_commander: str = ""
    duration_hours: float | None = None
    interpretation_notes: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Agent turn outputs (produced by ares/agents/*, consumed by orchestrator)
# ---------------------------------------------------------------------------
# Department message types
PROPOSAL = "PROPOSAL"
OBJECTION = "OBJECTION"
COUNTEROFFER = "COUNTEROFFER"
ACCEPT_MODE = "ACCEPT_MODE"
REFUSE_SACRIFICE = "REFUSE_SACRIFICE"
ACCEPT_SACRIFICE = "ACCEPT_SACRIFICE"
DEPT_MSG_TYPES = (PROPOSAL, OBJECTION, COUNTEROFFER, ACCEPT_MODE, REFUSE_SACRIFICE, ACCEPT_SACRIFICE)

# All transcript message types (stored in messages.msg_type)
MSG_TYPES = DEPT_MSG_TYPES + (
    "BRIEFING", "CONFLICT", "REQUEST", "COMMITMENT", "COMMITMENT_ACCEPTED",
    "COMMITMENT_REJECTED", "COMMITMENT_VOIDED", "PLAN_DRAFT", "VALIDATION",
    "VOTE_CALL", "VOTE", "VOTES_CLEARED", "APPROVAL", "APPROVAL_BLOCKED",
    "EVENT", "PLAN_STALE", "DEADLOCK", "INFEASIBLE", "TIMEOUT", "SYSTEM", "FACT_CHECK",
)


@dataclass
class DepartmentTurn:
    agent: str
    msg_type: str                      # one of DEPT_MSG_TYPES
    mode: str                          # own selected mode code, e.g. "L2"
    message: str                       # natural-language statement shown in transcript
    reason: str = ""
    consequence: str = ""              # what this department loses in the chosen mode
    offers: list[CommitmentOffer] = field(default_factory=list)
    accept_commitment_ids: list[str] = field(default_factory=list)
    reject_commitment_ids: list[str] = field(default_factory=list)
    refuses_sacrifice: bool = False
    proposed_combo: dict[str, str] | None = None   # full-combination counteroffer
    source: str = "llm"                # "llm" | "rule-based"
    fallback_reason: str | None = None
    provider: str | None = None
    model: str | None = None


@dataclass
class CommanderTurn:
    message: str
    target_combo: dict[str, str] | None = None     # Commander's proposed combination
    mode_requests: dict[str, str] = field(default_factory=dict)   # dept -> requested mode
    sacrifice_candidates: list[str] = field(default_factory=list)
    offers: list[CommitmentOffer] = field(default_factory=list)
    use_crisis_override: bool = False
    declare_infeasible: bool = False
    source: str = "llm"
    fallback_reason: str | None = None
    provider: str | None = None
    model: str | None = None


@dataclass
class AgentContext:
    """Read-only snapshot the orchestrator hands to an agent for one turn."""
    scenario_id: int
    scenario_kind: str                 # BASELINE | EVENT
    round_no: int
    max_rounds: int
    min_rounds: int
    phase: str                         # "PROPOSAL" | "NEGOTIATION" | "VOTE"
    pool: dict[str, int]
    policy: Policy
    override_available: bool           # True only in EVENT scenarios
    declared_modes: dict[str, str | None]   # each dept's latest declared mode
    plan: dict[str, Any] | None        # current plan record (see storage plan dict)
    validation: ValidationResult | None
    candidates: list[dict[str, Any]]   # optimizer output (see ares/optimizer.py)
    infeasibility: dict[str, Any] | None
    commitments: list[Commitment]      # all commitments in this scenario
    transcript: list[dict[str, Any]]   # recent public messages (oldest first)
    sacrifice_requests: dict[str, int]  # dept -> round it was first asked to sacrifice
    event: CrisisEvent | None = None
    forbidden_modes: list[str] = field(default_factory=list)
    commander_requests: dict[str, str] = field(default_factory=dict)  # this round's requests
    # Seconds left in the scenario's wall-clock budget (PDF p8: 3 minutes after an event).
    # Agents cap their LLM timeout to this and use the labelled rule-based fallback when it is < ~4 s.
    time_budget_s: float | None = None
