# ARES Accord — Product Requirements Document (PRD)

Status: **in development** (hackathon build). Owner: product-architect. Last update: 2026-10-10.
Sources of truth, in order: (1) the official Participant Challenge Guide **v2.1**; (2) the official
[starter repository](https://github.com/lifix-prog/Loopverse-AI_ML_Virtual_Module) at `d598411`; organizer files are
not bundled in this source release. (3) `docs/CONTRACT.md` + `ares/models.py`. Conflicts between (1) and (2) are resolved in
[`docs/DECISIONS_AND_RISKS.md`](docs/DECISIONS_AND_RISKS.md) (IDs `D1`–`D16`). Every requirement ID below is traced in
[`docs/REQUIREMENTS_TRACEABILITY.md`](docs/REQUIREMENTS_TRACEABILITY.md).

PDF page map: p1 cover · p2 Challenge overview · p3 Scenario · p4 Resources · p5 Negotiation · p6 Sacrifice & return ·
p7 Product requirements · p8 Emergency update · p9 Submission · p10 Final checklist.

---

## 1. Vision and problem

A micrometeorite storm hit Ares Colony (42 people): solar field damaged, slow habitat leak, greenhouse cooling
disrupted, six crew injured (p3). Four departments need the same limited pool; even all-Restricted
(L2+M2+F2+E2 = P83/W51/O60/R30/B18, §3) exceeds it, so **one department must accept a Sacrifice mode and receive a fair
return** (p3, p4). After the first agreement the judge injects an unseen emergency and the council must renegotiate (p8).

**Product:** a local-first, $0 multi-agent system — a Commander plus four department agents with private goals, state
and memory — that negotiates through a system-mediated message bus, is checked by a **deterministic validator outside
any LLM**, requires four ACCEPT votes on the same plan version for approval, recovers from injected events, and is
operated by judges entirely through a **Streamlit dashboard** (mandatory, p2/p7, RULES.md §4).

**Success =** a judge can enter a pool, start, watch >= 3 genuine rounds end in a validated, voted, versioned plan,
inject any crisis from the UI, see the old plan go STALE and a new APPROVED plan or justified INFEASIBLE within ~3
minutes, and export the plan + full transcript.

**Non-goals:** time simulation of event durations (D5), hosted deployment (no budget), changing package values,
per-agent different models (allowed but not required, p10).

---

## 2. Agents

| Agent | ID | Immediate mission (p3) | Main concern (p3) |
|---|---|---|---|
| Commander | `COMMANDER` | Coordinate the plan and approve it | Colony-wide safety |
| Life Support | `LIFE_SUPPORT` | Seal the habitat and stabilize the air | Oxygen and continuous power |
| Medical | `MEDICAL` | Stabilize and treat the injured crew | Power, oxygen, and bandwidth |
| Food Production | `FOOD` | Protect the crop cycle and cooling loop | Water and power |
| Engineering | `ENGINEERING` | Repair generation and critical systems | Robot time and bandwidth |

| ID | Requirement | Source |
|---|---|---|
| AG-01 | Five named agents load with **separate goals, constraints, state and message history**. | p2, p7, p10 |
| AG-02 | Agents may share one model but must be able to **disagree, change requests, make commitments and vote independently**. | p2, p10 |
| AG-03 | Real message exchange through the system; no static conversation, prepared transcript, or one model writing five viewpoints in a single call. Each agent turn is a separate call with its own private context. | p2, p7 |
| AG-04 | Commander receives the state first, shares it with all four departments, opens Round 1, manages rounds, drafts numbered plans, records the final decision. | p2, p3, p5 |
| AG-05 | Commander **cannot bypass** risk limits, return commitments, the validator or votes (approval decided by a deterministic gate, not by the Commander's text). | p2, p7 |
| AG-06 | Agents may change modes but **cannot edit resource values** inside a published package; a department may only select its own codes (output sanitisation). | p4, p7 |
| AG-07 | No participant manually writes agent messages during a run. | p7, p9 |
| AG-08 | Agent memory across rounds and scenarios (private per agent, persisted). | starter checklist (bonus) |
| AG-09 | LLM-driven agents are the primary mode; a rule-based policy is an explicitly labelled fallback/demo mode (D9). | RULES.md §5, p7 |

---

## 3. Resources and mode packages (immutable, p4)

Baseline pool (judge-editable, p3/p4/p7):

| Power | Water | Oxygen | Robot time | Bandwidth |
|---|---|---|---|---|
| 79 | 52 | 59 | 26 | 17 |

| Code | Department | Tier | Power | Water | Oxygen | Robot | Bandwidth | Risk | Consequence |
|---|---|---|---|---|---|---|---|---|---|
| L1 | Life Support | Standard | 34 | 10 | 42 | 4 | 4 | 1 | Protects the full mission |
| L2 | Life Support | Restricted | 27 | 9 | 36 | 6 | 3 | 5 | Completes immediate task, creates recovery debt |
| L3 | Life Support | Sacrifice | 23 | 8 | 33 | 2 | 2 | 9 | Living zone closes; crew crowding and thermal limits continue for 48 hours |
| M1 | Medical | Standard | 28 | 12 | 18 | 3 | 10 | 1 | Protects the full mission |
| M2 | Medical | Restricted | 21 | 10 | 14 | 2 | 6 | 5 | Completes immediate task, creates recovery debt |
| M3 | Medical | Sacrifice | 18 | 8 | 12 | 2 | 5 | 9 | One surgery is delayed and the patient remains unstable |
| F1 | Food Production | Standard | 24 | 34 | 8 | 4 | 2 | 1 | Protects the full mission |
| F2 | Food Production | Restricted | 15 | 27 | 5 | 4 | 2 | 5 | Completes immediate task, creates recovery debt |
| F3 | Food Production | Sacrifice | 10 | 24 | 5 | 2 | 1 | 9 | One crop bay is abandoned; food reserve falls by 40 percent |
| E1 | Engineering | Standard | 26 | 6 | 6 | 22 | 10 | 1 | Protects the full mission |
| E2 | Engineering | Restricted | 20 | 5 | 5 | 18 | 7 | 5 | Completes immediate task, creates recovery debt |
| E3 | Engineering | Sacrifice | 16 | 4 | 4 | 14 | 5 | 9 | The rover is cannibalized; future repair capacity is reduced |

All-Restricted L2+M2+F2+E2 = P83 / W51 / O60 / R30 / B18 — exceeds the pool in four resources (p4).

---

## 4. Functional requirements

### 4.1 Scenario start
| ID | Requirement | Source |
|---|---|---|
| FR-01 | Judge enters the five resource values and starts the scenario from the UI; default = baseline pool. | p3, p4, p7, p10 |
| FR-02 | The 12 packages above are fixed; values and risk never change at runtime. | p4, p10 |
| FR-03 | Each department selects exactly one complete, published mode package of its own. | p3, p4, p6 |
| FR-04 | No preloaded final allocation, winning plan, transcript or decision sequence. | p3, p9, RULES.md §5 |

### 4.2 Negotiation protocol (p5 steps 1–7)
| ID | Requirement | Source |
|---|---|---|
| FR-05 | **Publish:** Commander shares current resources, event (if any), plan version and round limit (BRIEFING). | p5 §1 |
| FR-06 | **Request:** each department requests Standard and explains what it loses in Restricted/Sacrifice (PROPOSAL with mode, consequence, reason). | p5 §2 |
| FR-07 | **Conflicts:** Commander compares packages with resource, risk and sacrifice limits (CONFLICT message with numbers). | p5 §3 |
| FR-08 | **Negotiate:** agents change modes, reject unfair sacrifice, offer compensation to the sacrificing department. | p5 §4 |
| FR-09 | **Draft:** Commander creates a numbered plan with four mode packages and the return commitments. | p5 §5 |
| FR-10 | **Validate & vote:** validator checks the plan; if PASS, all four departments vote on that exact version. | p5 §6 |
| FR-11 | **Approve or continue:** approval only after four ACCEPT votes; otherwise another round. | p5 §7 |
| FR-12 | >= 3 rounds before the first approval (enforced by the approval gate, `min_rounds=3`). | p5, p10 |
| FR-13 | >= 1 rejected or revised proposal per opening negotiation (tracked + shown as protocol evidence, D14). | p5 |
| FR-14 | >= 1 agent refuses Sacrifice before the final agreement (tracked + shown, D14). | p5 |
| FR-15 | Maximum round limit and timeout; clear terminal result: `APPROVED`, `DEADLOCK` (max rounds), `INFEASIBLE`, `TIMEOUT` (deadline, D16). | p5, p7 |
| FR-16 | Transcript message types carry the p5 contents: Proposal (mode, consequence, reason); Objection (resource conflict / unfair sacrifice / risk limit / missing return); Counteroffer (different combination + compensation terms); Commitment (owner, beneficiary, promised return, expiry); Vote (ACCEPT/REJECT, plan version, reason); Approval (version + final validation). | p5 |

### 4.3 Sacrifice and compensation
| ID | Requirement | Source |
|---|---|---|
| FR-17 | Baseline policy: combined risk <= 24 and <= 1 Sacrifice mode. | p4, p6, p10 |
| FR-18 | Each sacrificing department has >= 2 **ACCEPTED** return commitments from **different** agents (owner != beneficiary; Commander counts as an agent; "accepted" = accepted by the beneficiary in the transcript, D11). | p4, p5, p6, p10 |
| FR-19 | PDF return templates (§5) are offered to agents as defaults; custom commitments are allowed if they carry owner, beneficiary, promise and expiry. | p5, p6 |
| FR-20 | Commitment backing: package-sourced returns must fit the owner's selected package; Commander reserve returns must fit the unallocated reserve; two ACCEPTED commitments with the same exclusive claim -> FAIL (D12); "future" pledges from a sacrificing owner -> warning (D13). | p6 (derived) — ASSUMPTION |

### 4.4 Validator, versioning and votes
| ID | Requirement | Source |
|---|---|---|
| FR-21 | Deterministic validator outside the LLM with checks: Mode packages, Resources, Risk, Sacrifice limit, Return agreement, Commitment backing, Policy, Votes. | p6, p7 |
| FR-22 | Validator blocks unsafe plans and returns PASS/FAIL with specific reasons (numbers per over-limit resource). | p7, p10 |
| FR-23 | Every candidate plan has a version number; numbering is global per session (post-event versions continue the sequence, D15). | p5, p10 |
| FR-24 | Any plan change creates a new version and clears earlier votes (VOTES_CLEARED message). | p5, p7 |
| FR-25 | Approval = validator PASS + exactly four ACCEPT votes on the same **current** version + round >= min rounds. | p2, p5, p6, p10 |
| FR-26 | Final plan shows: mode per department, its fixed package, the sacrifice and return agreement, remaining reserve, total risk, all four votes, Commander's decision. | p3 |
| FR-27 | If no feasible plan exists: `INFEASIBLE` with blocking constraints and the specific external resource or policy change needed (e.g. "+4 Power" or Crisis Override). Optimizer = decision support only. | p3, p10 |

### 4.5 Emergency injection and recovery (p8)
| ID | Requirement | Source |
|---|---|---|
| FR-28 | Judge injects an event from the dashboard without code changes: official JSON (`event_injection_format.json`), absolute changes, percent changes, form, or free text (D5). Preview before apply. | p2, p8, starter `event_injection_format.json` |
| FR-29 | Record the event (stored, shown in transcript) and update the shared resource/priority state. | p8 §1 |
| FR-30 | Mark the previously approved plan `STALE` when the event breaks it (shown in UI). | p8 §2 |
| FR-31 | Review commitments: void those whose cost or owner is no longer valid; carry valid ones as PROPOSED for re-acceptance. | p8 §3 |
| FR-32 | Crisis Override only after an event: risk limit 28 and a second Sacrifice; both sacrificing departments need complete return agreements. Validator only accepts policies (24,1) or (28,2) (D10). | p8 |
| FR-33 | >= 2 visible rounds after an event unless infeasibility is proven immediately. | p8 §4 |
| FR-34 | New approval requires four fresh votes on the new plan version. | p8 §5 |
| FR-35 | New APPROVED plan or justified INFEASIBLE within 3 minutes of the event (`ARES_SCENARIO_DEADLINE_S=170`, D16). | p8 |
| FR-36 | Works for unseen events: no hardcoded events or fixed configurations. | p8 |

### 4.6 Dashboard (p7, RULES.md §4)
| ID | View | Must show / do |
|---|---|---|
| FR-37 | Mission Control | Current event, available resources, used resources, reserve, round, plan status, current plan version |
| FR-38 | Council Transcript | Persistent full history of **all scenarios** (baseline + every event): agent, message type, round, plan version, source badge (LLM / rule-based fallback / system / validator) |
| FR-39 | Mode Board | Each department's selected package, consequence, risk, return commitments, conflicts |
| FR-40 | Validation & Votes | PASS/FAIL with specific reasons per check; vote table for current version |
| FR-41 | Judge Controls (sidebar) | Start (enter resources, provider, max rounds), inject event, reset session, wipe history, export |
| FR-42 | Global | Current plan and its version visible throughout the run; live progress while negotiating |
| FR-43 | Global | Any fallback is clearly labelled per message/vote (`source`, `fallback_reason`) |
| FR-44 | Scenarios | List of all scenarios with status (RUNNING/APPROVED/DEADLOCK/INFEASIBLE/TIMEOUT/STALE) |

### 4.7 Exports
| ID | Requirement | Source |
|---|---|---|
| FR-45 | Final plan and negotiation log exportable as JSON **and** CSV from the UI. | p2, p7, p10 |
| FR-46 | Starter files: `final_allocation.json` (one `output_schema.json`-shaped record per agent, D3/D4), `council_transcript.json/.csv/.txt`, `crisis_handling_log.txt`, `final_plan.json` (D7). | starter `submission_checklist.md` |
| FR-47 | Architecture diagram: Mermaid in `docs/ARCHITECTURE.md`; `architecture_diagram.png` export is a team TODO (D7). | p9, starter checklist |

### 4.8 Bonus (starter checklist)
| ID | Requirement | Source |
|---|---|---|
| FR-48 | `flag_human_review = risk_score > 20` on exported records; optional judge co-sign toggle (AWAITING_COSIGN -> APPROVED / VETOED), off by default (D8). | starter checklist |
| FR-49 | Agent memory across rounds (= AG-08). | starter checklist |
| FR-50 | Graceful handling of communication failure / noisy messages (JSON repair, sanitisation, labelled fallback). | starter checklist, p7 |

---

## 5. Return templates (p6, encoded in `RETURN_TEMPLATES`)

| Sacrifice | What is lost | Return 1 | Return 2 |
|---|---|---|---|
| L3 Life Support | Living zone closes; crowding/thermal limits for 48 h | `L3-ENG` Engineering commits 4 of its Robot units to habitat inspection (package) | `L3-CMD` Commander gives habitat restoration first priority next cycle (claim `commander_next_cycle_first_priority`) |
| M3 Medical | One surgery delayed; patient unstable | `M3-LS` Life Support reserves 3 of its Oxygen units for care (package) | `M3-CMD` Commander gives Medical first priority next cycle (claim `commander_next_cycle_first_priority`) |
| F3 Food | One crop bay abandoned; food reserve -40% | `F3-ENG` Engineering commits 4 future Robot units to greenhouse rebuild (future) | `F3-CMD` Commander protects the next Water increase for Food (claim `next_water_increase`) |
| E3 Engineering | Rover cannibalized; repair capacity reduced | `E3-CMD` Commander assigns the 2-unit Water reserve to rover cooling (reserve) | `E3-FOOD` Food gives Engineering first priority on the next recovered Water (claim `next_recovered_water`) |

Default expiry: "End of next planning cycle" (ASSUMPTION — the PDF gives no expiry values).

---

## 6. Negotiation, voting and versioning rules (summary)

1. Round 1 = PROPOSAL phase: every department requests Standard (FR-06). Commander drafts plan v1, validator runs (FAIL expected on baseline).
2. Rounds >= 2: Commander turn (targets, sacrifice candidates, offers) -> departments (sacrifice candidates speak last) -> if any mode/commitment changed, a **new plan version** is created and votes cleared -> validate -> if PASS and round >= `min_rounds`, VOTE_CALL -> four votes -> deterministic approval gate.
3. A department never accepts Sacrifice in the same round it is first asked; it accepts only when >= 2 return commitments from different agents are on the table (agent stance, CONTRACT §5).
4. A department votes REJECT if the plan fails validation or its own sacrifice lacks two accepted returns.
5. Max rounds reached without approval -> `DEADLOCK`; deadline -> `TIMEOUT` (old plan stays STALE); no feasible combination even with allowed policy -> `INFEASIBLE` + suggestions.

---

## 7. Error handling

| Failure | Behaviour |
|---|---|
| LLM HTTP error / quota / timeout | Bounded retry with timeout + pacing (`ARES_LLM_TIMEOUT_S`, `ARES_LLM_MAX_RETRIES`, `ARES_LLM_MIN_INTERVAL_S`); then **rule-based fallback for that turn only**, labelled `source=rule-based` + `fallback_reason`. |
| Invalid / non-JSON model output | One JSON-repair retry, then labelled fallback. |
| Mode code not owned by the department / unknown | Sanitised: keep previous declared mode; note in transcript. |
| Commitment ids that don't exist / not addressed to agent | Ignored with a note. |
| Unparseable event | `ValueError` shown in UI; nothing applied. Interpretation notes always shown. |
| Pool would go negative | Clamped at 0. |
| Missing API key for chosen provider | Provider shown as unavailable; run uses rule-based with a visible banner. |
| Non-convergence | `DEADLOCK` after max rounds. |
| Post-event deadline | `TIMEOUT`, no approval. |

## 8. Non-functional requirements

| ID | Requirement | Source |
|---|---|---|
| NFR-01 | Handles invalid model output, timeouts and maximum rounds. | p7 |
| NFR-02 | Safe fallback for temporary model/network failure, clearly labelled. | p7 |
| NFR-03 | API keys only via environment variables; `.env` ignored; `.env.example` with names only; no secrets/private data in repo. | p7, p9, p10, RULES.md §6 |
| NFR-04 | Documented startup and reset steps (README). | p7, p10 |
| NFR-05 | Reliable, small, reproducible; works from a clean setup with `pip install -r requirements.txt`. | p7, p10 |
| NFR-06 | Structured mode, risk, commitment and vote records (dataclasses + SQLite tables). | p7 |
| NFR-07 | Persistent history across scenarios and restarts (SQLite `data/ares.db`). | p7 |
| NFR-08 | $0 budget: free-tier/local LLMs only; runs locally on Windows/macOS/Linux, Python 3.13. | project constraint — ASSUMPTION |
| NFR-09 | Model output is data only: no eval/exec/shell; sanitised before use. | p7 and implementation security requirements |
| NFR-10 | Every HTTP call has a timeout and bounded retry. | p7 |
| NFR-11 | Test suite runs offline (no network) with `pytest -q`. | Repository quality gate; see README test instructions |
| NFR-12 | No hardcoded conversations/plans/events; no fabricated logs. | p9, RULES.md §5 |
| NFR-13 | External libraries, APIs, AI coding tools and pre-existing code declared. | p9 |
| NFR-14 | Core implementation built within the 24-hour window. | p9, RULES.md §1 |

## 9. Submission requirements

| ID | Item | Source |
|---|---|---|
| SUB-01 | Source repository: complete code, configuration example, dependency file, attributions. | p9 |
| SUB-02 | README: architecture, setup, run command, model choices, test steps, known limitations. | p9 |
| SUB-03 | Runnable demo: local application with clear startup instructions (no hosted link, $0). | p9 |
| SUB-04 | Architecture diagram: agents, shared state, message flow, validator, storage, models, interface — matches implementation. | p9, p10 |
| SUB-05 | Evidence: one opening negotiation log, one post-event log, final plan files (exported, unedited). | p9, p10 |
| SUB-06 | Demo video 3–5 min: negotiation, approval, event injection, recovery (using an LLM provider, D9). | p9, RULES.md §6 |
| SUB-07 | Team declaration: names, roles, models/APIs, libraries, pre-existing code. | p9 |
| SUB-08 | GitHub repository link (public/private) submitted. | RULES.md §6 |

## 10. Definition of done

- [ ] `pytest -q` green offline; validator tests cover every check and both baseline paths.
- [ ] Rule-based headless run (`scripts/smoke_run.py`): baseline APPROVED after >= 3 rounds with a recorded refusal and a revised/rejected proposal; each practice event -> STALE, commitments reviewed, override, >= 2 rounds, fresh votes, APPROVED.
- [ ] Same flow completes in the dashboard with at least one LLM provider; fallback turns labelled.
- [ ] Unseen event typed as free text and as official JSON is parsed, previewed and handled (APPROVED or INFEASIBLE with suggestion) within 3 minutes.
- [ ] Exports download (JSON + CSV + starter files) and match the transcript.
- [ ] README clean-setup steps verified on a fresh venv; no secrets in repo; diagram matches code.
- [ ] Evidence captured from genuine runs; video recorded; team declaration complete.

---

## Appendix A — Verified scenario math (orchestrator enumeration of all 81 combinations)

Baseline (79/52/59/26/17, risk <= 24, <= 1 sacrifice) has **exactly two** valid plans (p6):

| Path | Modes | Totals | Risk | Reserve | Sacrifice |
|---|---|---|---|---|---|
| A | L3+M2+F2+E2 | P79/W50/O57/R26/B17 | 24 | W2, O2 | Life Support |
| B | L2+M2+F2+E3 | P79/W50/O59/R26/B16 | 24 | W2, B1 | Engineering |

Every practice event (p8) makes the baseline **infeasible without Crisis Override**. Feasible counts with override
(risk <= 28, <= 2 sacrifices):

| Event | Pool change | Feasible plans (with override) |
|---|---|---|
| Solar aftershock | Power -4 | 3: L2+M2+F3+E3, L3+M2+F2+E3, L3+M2+F3+E2 |
| Water contamination | Water -3 | 5: L2+M2+F3+E3, L2+M3+F2+E3, L3+M2+F2+E3, L3+M2+F3+E2, L3+M3+F2+E2 |
| Secondary oxygen leak | Oxygen -3 | 2: L3+M2+F2+E3, L3+M3+F2+E2 |
| Rover actuator failure | Robot -4 | 1: L3+M2+F2+E3 (risk 28) |
| Relay interference | Bandwidth -2 | 3: L2+M2+F3+E3, L2+M3+F2+E3, L3+M2+F2+E3 |

Derived (product-architect, to be confirmed by `tests/test_optimizer.py`): with **PDF templates only**, some of these
fail "Commitment backing" — L3+M3 needs the Commander's exclusive "first priority next cycle" for both (D12 -> FAIL);
E3 needs a Water reserve >= 2 (fails in Water -3 for L3+M2+F2+E3 (reserve 0) and L2+M3+F2+E3 (reserve 1)). F3+E3 passes
with a warning (Engineering's future pledge while sacrificing, D13). Agents can still reach those plans with custom,
valid commitments.
