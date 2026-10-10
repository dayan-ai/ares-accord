# ARES Accord — Shared Interface Contract (v1)

Owner: orchestrator (main session). This file + `ares/models.py` are the single source of truth
for cross-module interfaces. Do **not** change signatures without updating this file.

Python 3.13, stdlib + `requests`, `python-dotenv`, `streamlit`, `pandas`, `pytest`. Run everything with
`.venv/Scripts/python` (Windows) from the repo root. Imports are absolute: `from ares.models import ...`.

## 1. Module ownership

| Module | Owner agent | Depends on |
|---|---|---|
| `ares/models.py` | orchestrator (main) | — |
| `ares/validator.py`, `ares/optimizer.py`, `ares/events.py`, `tests/test_validator.py`, `tests/test_optimizer.py`, `tests/test_events.py` | backend-validator | models |
| `ares/llm/*`, `ares/agents/*`, `tests/test_llm.py`, `tests/test_agents.py` | ai-agent-engineer | models (+ optimizer output shape below) |
| `ares/storage.py`, `ares/orchestrator.py`, `ares/service.py`, `ares/exports.py`, `ares/config.py` | orchestrator (main) | all |
| `app.py`, `ares/ui/*` | frontend-uiux | service API (section 6) |
| `tests/test_acceptance.py`, `tests/test_orchestrator.py` | qa-testing | all |
| `.env.example`, `.gitignore`, `requirements.txt`, `scripts/*` | security-devops | — |
| `README.md`, `PRD.md`, public-facing `docs/*` (except CONTRACT.md) | docs-submission / product-architect | — |

## 2. Validator — `ares/validator.py`

```python
def plan_totals(modes: dict[str, str]) -> tuple[dict[str, int], int]
    # sum of the selected packages' resources, and summed risk. Unknown codes ignored.

def validate_plan(modes: dict[str, str], commitments: list[Commitment], pool: dict[str, int],
                  policy: Policy, forbidden_modes: Sequence[str] = ()) -> ValidationResult
```
Checks, each appended as a `CheckResult` (names exactly as below), status PASS only if all pass:
1. **"Mode packages"** — exactly one published code per department (all 4 present, code belongs to that dept,
   code exists in `MODE_PACKAGES`, not in `forbidden_modes`).
2. **"Resources"** — totals <= pool for every resource (report each over-limit resource with numbers).
3. **"Risk"** — total risk <= `policy.risk_limit`.
4. **"Sacrifice limit"** — number of SACRIFICE-tier modes <= `policy.max_sacrifices`.
5. **"Return agreement"** — for every sacrificing dept: >= 2 commitments with `status=="ACCEPTED"`,
   `beneficiary==dept`, from >= 2 **different** owners, owner != beneficiary, non-empty `promise` and `expiry`.
6. **"Commitment backing"** — over ACCEPTED commitments whose beneficiary is currently sacrificing:
   `source=="package"`: owner must be a department and sum(amount) per (owner, resource) <= owner's selected package value;
   `source=="reserve"`: owner must be COMMANDER and sum(amount) per resource <= reserve (pool - totals);
   `claim` keys: two ACCEPTED commitments with the same claim and different beneficiaries -> FAIL;
   `source=="future"` from an owner that is itself sacrificing -> **warning only**.
7. **"Policy"** — `policy` is either baseline (24/1) or override (28/2); if `crisis_override` is used the
   orchestrator only allows it in EVENT scenarios (validator just checks internal consistency).

`reasons` = details of failed checks; `reserve` = pool - totals (may be negative); `sacrifices` = depts in SACRIFICE.

```python
def check_votes(votes: list[Vote], plan_version: int) -> CheckResult
    # name "Votes": PASS iff exactly one vote per department (4), all decision ACCEPT, all plan_version == plan_version.
def approval_gate(validation: ValidationResult, votes: list[Vote], plan_version: int,
                  current_version: int, round_no: int, min_rounds: int) -> tuple[bool, list[str]]
    # ok only if: plan_version == current_version, validation.passed, check_votes passes, round_no >= min_rounds.
    # Returns all blocking reasons. The Commander cannot bypass this.
```

## 3. Optimizer — `ares/optimizer.py` (decision support only; never approves)

```python
def enumerate_candidates(pool, policy, forbidden_modes=()) -> list[dict]
```
Every combination (3^4 = 81) that passes resources + risk + sacrifice limit, sorted by (risk asc, #sacrifices asc,
min reserve desc). Each dict:
`{"modes": {dept: code}, "totals": {...}, "reserve": {...}, "risk": int, "sacrifices": [dept,...],
  "template_returns_ok": bool, "template_return_issues": [str], "label": "L3+M2+F2+E2"}`
(`template_returns_ok` = would the PDF return templates for its sacrifices pass "Commitment backing").

```python
def diagnose_infeasibility(pool, policy, forbidden_modes=(), override_allowed=False) -> dict
```
`{"feasible": bool, "feasible_with_override": bool, "blocking": [str], "nearest": {"modes":..., "label":...,
  "shortfall": {res: int}}, "suggestions": [str]}` — `suggestions` names the specific external resource
increase (e.g. "+4 Power") and/or policy change (Crisis Override) that would unblock a plan.

## 4. Events — `ares/events.py`

```python
def parse_event(data: dict | str) -> CrisisEvent       # raises ValueError on unusable input
def apply_event(pool: dict[str, int], event: CrisisEvent) -> tuple[dict[str, int], list[str]]
```
Must accept (a) the official starter format (`event_injection_format.json`: `impact` dict with keys like
`power_reduction_percent`, plus `event_id`, `event_name`, `description`, `requires_replan`,
`message_to_commander`, `impact.duration_hours`, `impact.affected_systems`), (b) simple absolute form
`{"name":..., "changes": {"power": -4}}` / `resource_changes`, (c) keys like `water_reduction`, `oxygen_decrease`,
`robot_time_reduction`, `bandwidth_delta`, `power_increase`, `<res>_set`, (d) free-text descriptions such as
"Available Power decreases by 4" / "power drops by 30%". Resource synonyms: power/energy/kwh/solar/electricity;
water/h2o/liters; oxygen/o2/air; robot/robot_time/robots/rover/robotics; bandwidth/comms/relay/communication/data.
Optional `forbidden_modes` list. Percent reductions remove `ceil(pool*pct/100)` (conservative). Pool never < 0.
Every interpretation decision is appended to `event.interpretation_notes`.

## 5. Agents — `ares/agents/*` and LLM — `ares/llm/*`

```python
# ares/llm/base.py
class LLMError(Exception)            # any provider failure (HTTP, timeout, quota, empty)
class Provider(Protocol):
    name: str; model: str
    def complete_json(self, system: str, user: str, timeout_s: float) -> dict  # raises LLMError
def available_providers() -> list[dict]   # [{"name","model","available","env_var","note"}]
def get_provider(name: str) -> Provider | None   # None for "rule-based"; raises LLMError if key missing
```
Providers: `gemini`, `groq`, `openrouter`, `ollama`, plus `rule-based` (no LLM). Keys from env only.

```python
# ares/agents/__init__.py exports:
class DepartmentAgent:
    def __init__(self, dept: str, provider: Provider | None)
    agent_id: str; profile: dict; memory: list[dict]; state: dict   # private, per-agent
    def take_turn(self, ctx: AgentContext) -> DepartmentTurn
    def vote(self, ctx: AgentContext) -> Vote
    def export_state(self) -> dict ; def load_state(self, d: dict) -> None
class CommanderAgent:
    def __init__(self, provider: Provider | None)
    def take_turn(self, ctx: AgentContext) -> CommanderTurn
    def narrate(self, ctx: AgentContext, kind: str, facts: dict) -> str   # BRIEFING/APPROVAL/etc text; never decides
    def export_state(self) -> dict ; def load_state(self, d: dict) -> None
def build_agents(provider_name: str) -> tuple[CommanderAgent, dict[str, DepartmentAgent]]
```
Rules: on LLMError / timeout / invalid JSON (after 1 repair retry) the agent falls back to `ares/agents/rule_policy.py`
for **that turn only** and sets `source="rule-based"`, `fallback_reason=...`. With provider None, every turn is
rule-based. Output sanitisation: a department may only pick its own mode codes; invalid -> keep previous declared mode.
Agents never edit package values, never approve plans, and never fabricate other agents' messages.

Protocol stance every department follows (LLM prompt + rule policy): request Standard in PROPOSAL phase and explain what
Restricted/Sacrifice would cost; never accept Sacrifice in the same round it is first requested; accept Sacrifice only when
>= 2 return commitments from different agents are on the table; offer return commitments when you are a template owner
for the sacrificing department; vote REJECT if the plan fails validation or your sacrifice lacks 2 accepted returns.

## 6. Service API for the UI — `ares/service.py`

```python
class Service:
    def providers(self) -> list[dict]
    def start_baseline(self, pool: dict[str,int], provider: str, max_rounds: int = 6,
                       on_message: Callable[[dict], None] | None = None) -> dict      # scenario dict
    def inject_event(self, event: dict | str, provider: str, max_rounds: int = 5,
                     on_message: Callable[[dict], None] | None = None) -> dict
    def preview_event(self, event: dict | str) -> dict  # {"event": CrisisEvent dict, "new_pool": {...}, "changes": [...]}
    def reset_session(self) -> None          # new session; history kept
    def wipe_all(self) -> None               # delete DB contents
    def state(self) -> dict                  # dashboard snapshot, below
    def transcript(self, session_id: int | None = None, scenario_id: int | None = None) -> list[dict]
    def export_bundle(self, session_id: int | None = None) -> dict   # {"filename": bytes|str}
def get_service() -> Service
```
`state()` returns:
```
{"session": {"id", "created_at"} | None,
 "scenario": {"id","session_id","kind","name","description","pool":{},"base_pool":{},"policy":{},
              "event":{}|None,"status":"RUNNING|APPROVED|DEADLOCK|INFEASIBLE|TIMEOUT|STALE",
              "result_reason":str,"round":int,"max_rounds":int,"provider":str,"started_at","finished_at"} | None,
 "plan": plan | None,                 # latest plan version of current scenario
 "approved_plan": plan | None,        # latest APPROVED plan in session (may now be STALE)
 "plans": [plan,...],                 # all versions in current scenario
 "commitments": [Commitment dict,...],
 "scenarios": [scenario,...],         # all scenarios (all sessions), newest first
 "protocol": {"rounds_before_approval": int, "revised_or_rejected": bool, "sacrifice_refused": bool,
              "return_commitments": int}}
```
plan dict: `{"id","scenario_id","version","modes":{dept:code},"commitment_ids":[...],"totals":{},"reserve":{},
"risk":int,"policy":{},"status":"DRAFT|FAILED|VOTING|REJECTED|APPROVED|SUPERSEDED|STALE","validation":ValidationResult dict,
"votes":[Vote dict],"round":int,"created_at"}`

message dict: `{"id","session_id","scenario_id","round","plan_version","sender","recipient","msg_type","content",
"payload":{},"source":"llm|rule-based|system|validator","provider","model","fallback_reason","created_at"}`

## 7. Contract amendments (v1.1, orchestrator)
- Plan status values: `DRAFT | VALIDATED | FAILED | VOTING | REJECTED | APPROVED | SUPERSEDED | STALE`
  (`VALIDATED` = validator PASS, not yet voted). Plans also carry a free-text `note` (e.g. why it became STALE).
- Candidate dicts handed to agents carry `requires_override: bool` (True = feasible only under Crisis Override).
  In EVENT scenarios the orchestrator appends override-only candidates until the Commander invokes the override.
- `CommanderAgent.narrate` is NOT required: factual announcements (briefing, plan drafts, validation, approval,
  deadlock/infeasible/timeout) are published by the orchestrator with `source="system"` / `"validator"`.
- Scenario statuses add `ERROR` (internal failure, safely stopped, nothing approved).
- `Service.state()` also returns `sessions` (all sessions) and `running` (bool). `Service.current_pool()` exists.
- Export bundle files: `final_plan.json`, `final_allocation.json` (starter-schema-shaped), `council_transcript.json|csv|txt`,
  `crisis_handling_log.txt`, `plans.csv`, `full_history_all_sessions.csv`.
- Human-in-the-loop (bonus): `Service.start_baseline/inject_event(..., require_cosign=False)` and
  `Service.cosign(approve: bool, note="")`. Scenario/plan status `AWAITING_COSIGN` (gate passed, risk > 20, judge must
  decide) -> `APPROVED` or scenario `VETOED` / plan `REJECTED`. Events cannot be injected while a co-sign is pending.
