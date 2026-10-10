# Requirements Traceability Matrix

Owner: product-architect. IDs are defined in [`PRD.md`](../PRD.md). "Basis": **CONFIRMED** = stated in the PDF
(guide v2.1, page cited) or a starter file; **ASSUMPTION** = our interpretation (see `docs/DECISIONS_AND_RISKS.md`).
"Status" reflects the current automated suite on 2026-10-10 (**258 passed**, 0 failed). Manual dashboard observations
are historical; this audit confirmed the running page responds but did not inspect every tab. "PASS (pytest)" means
the listed test files cover the row and the whole suite passed; it does not mean a live LLM was used.

## Agents

| ID | Requirement (short) | Source | Implementation | Test | Basis | Status |
|---|---|---|---|---|---|---|
| AG-01 | 5 named agents, separate goals/state/history | p2, p7, p10 | `ares/agents/`, `ares/storage.py` (agent_state) | tests/test_agents.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| AG-02 | Disagree, change requests, commit, vote independently | p2, p10 | `ares/agents/`, `ares/orchestrator.py` | tests/test_agents.py, tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| AG-03 | Real message exchange; one call per agent turn | p2, p7 | `ares/orchestrator.py`, `ares/agents/` | tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| AG-04 | Commander briefs first, opens R1, manages rounds, drafts plans, records decision | p2, p3, p5 | `ares/agents/` (CommanderAgent), `ares/orchestrator.py` | tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| AG-05 | Commander cannot bypass limits/returns/validator/votes | p2, p7 | `ares/validator.py` (approval_gate), `ares/orchestrator.py` | tests/test_validator.py, tests/test_acceptance.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| AG-06 | Change modes, never edit package values; own codes only | p4, p7 | `ares/models.py`, `ares/agents/` (sanitisation) | tests/test_agents.py, tests/test_validator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| AG-07 | No manual agent messages during run | p7, p9 | `app.py` (no message input), `ares/orchestrator.py` | Manual | CONFIRMED | PASS (manual: dashboard has no message input; verified 2026-10-10) |
| AG-08 | Agent memory across rounds | starter submission_checklist.md (bonus) | `ares/agents/`, `ares/storage.py` | tests/test_agents.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| AG-09 | LLM primary, rule-based labelled fallback/demo | RULES.md §5, p7 | `ares/agents/`, `ares/llm/` | tests/test_agents.py, tests/test_llm.py | ASSUMPTION (D9) | PASS (pytest, 258 passed 2026-10-10) |

## Scenario, protocol, sacrifice

| ID | Requirement (short) | Source | Implementation | Test | Basis | Status |
|---|---|---|---|---|---|---|
| FR-01 | Judge enters pool + starts from UI; baseline default | p3, p4, p7, p10 | `app.py`, `ares/service.py` | tests/test_acceptance.py + Manual | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-02 | 12 immutable packages | p4, p10 | `ares/models.py` | tests/test_validator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-03 | Exactly one own package per department | p3, p4, p6 | `ares/validator.py` ("Mode packages") | tests/test_validator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-04 | No preloaded allocation / transcript | p3, p9, RULES.md §5 | `ares/orchestrator.py`, `ares/agents/` | tests/test_acceptance.py (code scan) + Manual | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-05 | BRIEFING: resources, event, version, round limit | p5 §1 | `ares/orchestrator.py` | tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-06 | R1 PROPOSALs request Standard + state losses | p5 §2 | `ares/agents/` | tests/test_agents.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-07 | Commander states conflicts with numbers | p5 §3 | `ares/agents/`, `ares/validator.py` | tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-08 | Change modes, reject unfair sacrifice, offer compensation | p5 §4 | `ares/agents/`, `ares/orchestrator.py` | tests/test_agents.py, tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-09 | Numbered plan with 4 packages + returns | p5 §5 | `ares/orchestrator.py` | tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-10 | Validate; if PASS all 4 vote on exact version | p5 §6 | `ares/orchestrator.py`, `ares/validator.py` | tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-11 | Approve only after 4 ACCEPT else next round | p5 §7 | `ares/validator.py` (approval_gate) | tests/test_validator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-12 | >= 3 rounds before first approval | p5, p10 | `ares/validator.py`, `ares/orchestrator.py` | tests/test_validator.py, tests/test_acceptance.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-13 | >= 1 rejected/revised proposal (tracked) | p5 | `ares/orchestrator.py` (protocol flags) | tests/test_acceptance.py | CONFIRMED (tracking = ASSUMPTION D14) | PASS (pytest, 258 passed 2026-10-10) |
| FR-14 | >= 1 Sacrifice refusal (tracked) | p5 | `ares/agents/`, `ares/orchestrator.py` | tests/test_acceptance.py | CONFIRMED (tracking = ASSUMPTION D14) | PASS (pytest, 258 passed 2026-10-10) |
| FR-15 | Max rounds/timeout; APPROVED/DEADLOCK/INFEASIBLE/TIMEOUT | p5, p7 | `ares/orchestrator.py` | tests/test_orchestrator.py | CONFIRMED (TIMEOUT state = ASSUMPTION D16) | PASS (pytest, 258 passed 2026-10-10) |
| FR-16 | Message types carry p5 contents | p5 | `ares/models.py`, `ares/orchestrator.py`, `app.py` | tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-17 | Baseline risk <= 24, <= 1 Sacrifice | p4, p6, p10 | `ares/validator.py` ("Risk", "Sacrifice limit") | tests/test_validator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-18 | >= 2 ACCEPTED returns from different agents | p4, p5, p6, p10 | `ares/validator.py` ("Return agreement") | tests/test_validator.py | CONFIRMED ("accepted by beneficiary" = ASSUMPTION D11) | PASS (pytest, 258 passed 2026-10-10) |
| FR-19 | PDF return templates as defaults; custom allowed | p5, p6 | `ares/models.py` (RETURN_TEMPLATES), `ares/agents/` | tests/test_validator.py, tests/test_agents.py | CONFIRMED (custom = ASSUMPTION) | PASS (pytest, 258 passed 2026-10-10) |
| FR-20 | Commitment backing (package/reserve/claim/future) | p6 (derived) | `ares/validator.py` ("Commitment backing") | tests/test_validator.py | ASSUMPTION (D12, D13) | PASS (pytest, 258 passed 2026-10-10) |

## Validator, versioning, infeasibility

| ID | Requirement (short) | Source | Implementation | Test | Basis | Status |
|---|---|---|---|---|---|---|
| FR-21 | Deterministic validator outside LLM, all checks | p6, p7 | `ares/validator.py` | tests/test_validator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-22 | Blocks unsafe plans; specific reasons | p7, p10 | `ares/validator.py` | tests/test_validator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-23 | Every plan versioned; global per session | p5, p10 | `ares/orchestrator.py`, `ares/storage.py` | tests/test_orchestrator.py | CONFIRMED (global numbering = ASSUMPTION D15) | PASS (pytest, 258 passed 2026-10-10) |
| FR-24 | Plan change clears votes | p5, p7 | `ares/orchestrator.py`, `ares/validator.py` (check_votes) | tests/test_orchestrator.py, tests/test_validator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-25 | Approval = PASS + 4 ACCEPT current version + min rounds | p2, p5, p6, p10 | `ares/validator.py` (approval_gate) | tests/test_validator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-26 | Final plan contents (modes, packages, returns, reserve, risk, votes, decision) | p3 | `ares/service.py`, `app.py`, `ares/exports.py` | tests/test_acceptance.py + Manual | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-27 | INFEASIBLE + blocking constraints + specific change | p3, p10 | `ares/optimizer.py`, `ares/orchestrator.py` | tests/test_optimizer.py, tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |

## Events and recovery

| ID | Requirement (short) | Source | Implementation | Test | Basis | Status |
|---|---|---|---|---|---|---|
| FR-28 | Inject from UI: official JSON, absolute, percent, text | p2, p8, starter event_injection_format.json | `ares/events.py`, `app.py`, `ares/service.py` | tests/test_events.py | CONFIRMED (percent rounding = ASSUMPTION D5) | PASS (pytest, 258 passed 2026-10-10) |
| FR-29 | Record event; update shared state | p8 §1 | `ares/orchestrator.py`, `ares/storage.py` | tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-30 | Old plan -> STALE | p8 §2 | `ares/orchestrator.py` | tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-31 | Review commitments (void / re-propose) | p8 §3 | `ares/orchestrator.py` | tests/test_orchestrator.py | CONFIRMED (re-propose = ASSUMPTION) | PASS (pytest, 258 passed 2026-10-10) |
| FR-32 | Crisis Override only after event (28 / 2) | p8 | `ares/orchestrator.py`, `ares/validator.py` ("Policy") | tests/test_validator.py, tests/test_orchestrator.py | CONFIRMED (D10) | PASS (pytest, 258 passed 2026-10-10) |
| FR-33 | >= 2 visible rounds post-event unless proven infeasible | p8 §4 | `ares/orchestrator.py` | tests/test_acceptance.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-34 | Fresh 4 votes on new version | p8 §5 | `ares/orchestrator.py`, `ares/validator.py` | tests/test_acceptance.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-35 | New plan / INFEASIBLE within 3 min | p8 | `ares/orchestrator.py` (deadline) | tests/test_orchestrator.py + Manual (LLM timing) | CONFIRMED (170 s = ASSUMPTION D16) | PASS (pytest, 258 passed 2026-10-10) |
| FR-36 | Generalizes to unseen events | p8 | `ares/events.py`, `ares/optimizer.py`, `ares/agents/` | tests/test_events.py, tests/test_acceptance.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |

## Dashboard and exports

| ID | Requirement (short) | Source | Implementation | Test | Basis | Status |
|---|---|---|---|---|---|---|
| FR-37 | Mission Control view | p7, RULES.md §4 | `app.py` | Manual | CONFIRMED | PASS (manual, browser, rule-based run) |
| FR-38 | Council Transcript, persistent, all scenarios | p7, RULES.md §4 | `app.py`, `ares/storage.py` | tests/test_orchestrator.py (persistence) + Manual | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-39 | Mode Board view | p7 | `app.py` | Manual | CONFIRMED | PASS (manual, browser) |
| FR-40 | Validation & Votes view (PASS/FAIL + reasons) | p7, RULES.md §4 | `app.py` | Manual | CONFIRMED | PASS (manual, browser) |
| FR-41 | Judge Controls: start, resources, inject, reset, export | p7, RULES.md §4 | `app.py`, `ares/service.py` | Manual | CONFIRMED | PASS (manual: start, free-text inject, co-sign in browser; reset/export via UI smoke + tests) |
| FR-42 | Current plan + version always visible | p7 | `app.py` | Manual | CONFIRMED | PASS (manual, header badges) |
| FR-43 | Fallback clearly labelled | p7 | `ares/agents/`, `app.py`, `ares/exports.py` | tests/test_agents.py + Manual | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-44 | Scenario list with statuses | p7 (history across scenarios) | `app.py`, `ares/service.py` | Manual | ASSUMPTION | PASS (manual, Scenarios tab) |
| FR-45 | Export plan + log as JSON and CSV | p2, p7, p10 | `ares/exports.py` | tests/test_acceptance.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-46 | Starter output files (final_allocation.json etc.) | starter submission_checklist.md, output_schema.json | `ares/exports.py` | tests/test_acceptance.py | CONFIRMED (mapping = ASSUMPTION D3, D4, D7) | PASS (pytest, 258 passed 2026-10-10) |
| FR-47 | Architecture diagram (Mermaid; PNG TODO) | p9, starter checklist | `docs/ARCHITECTURE.md` | Manual | CONFIRMED | DONE (docs/architecture_diagram.png + Mermaid) |
| FR-48 | flag_human_review = risk > 20 | starter checklist (bonus) | `ares/exports.py` | tests/test_acceptance.py | ASSUMPTION (D8) | PASS (pytest, 258 passed 2026-10-10) |
| FR-49 | Agent memory across rounds | starter checklist (bonus) | `ares/agents/` | tests/test_agents.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| FR-50 | Graceful comm failure / noisy messages | starter checklist (bonus), p7 | `ares/llm/`, `ares/agents/` | tests/test_llm.py, tests/test_agents.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |

## Non-functional and submission

| ID | Requirement (short) | Source | Implementation | Test | Basis | Status |
|---|---|---|---|---|---|---|
| NFR-01 | Invalid output, timeouts, max rounds handled | p7 | `ares/llm/`, `ares/agents/`, `ares/orchestrator.py` | tests/test_llm.py, tests/test_agents.py, tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| NFR-02 | Safe labelled fallback | p7 | `ares/agents/rule_policy.py` | tests/test_agents.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| NFR-03 | Keys via env; no secrets | p7, p9, p10, RULES.md §6 | `.env.example`, `.gitignore`, `ares/llm/` | tests/test_llm.py + secret scan (T3.2) | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| NFR-04 | Startup + reset documented | p7, p10 | `README.md` | Manual | CONFIRMED | DONE (README, scripts/run.*, scripts/reset.*) |
| NFR-05 | Reproducible clean setup | p7, p10 | `requirements.txt`, `README.md` | Manual (fresh venv) | CONFIRMED | PARTIAL (previous clean-copy check reported 240 passed; current checkout has 258; recheck on the published clone) |
| NFR-06 | Structured mode/risk/commitment/vote records | p7 | `ares/models.py`, `ares/storage.py` | tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| NFR-07 | Persistent history (SQLite) | p7 | `ares/storage.py` | tests/test_orchestrator.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| NFR-08 | $0, local-first | project constraint | all | Manual | ASSUMPTION | PASS (offline rule-based mode; free-tier providers only) |
| NFR-09 | Model output is data only | p7, implementation requirement | `ares/agents/`, `ares/llm/` | tests/test_agents.py | ASSUMPTION (project rule) | PASS (pytest, 258 passed 2026-10-10) |
| NFR-10 | HTTP timeout + bounded retry | p7 | `ares/llm/` | tests/test_llm.py | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| NFR-11 | Offline test suite | project test command | `tests/` | `pytest -q` | ASSUMPTION (project rule) | PASS (pytest, 258 passed 2026-10-10) |
| NFR-12 | No hardcoded conversations / fabricated logs | p9, RULES.md §5 | all | tests/test_acceptance.py + review | CONFIRMED | PASS (pytest, 258 passed 2026-10-10) |
| NFR-13 | Declarations of libs/APIs/AI tools/code | p9 | `README.md`, submission form | Manual | CONFIRMED | PARTIAL (libraries/APIs declared; participant details and signed declarations remain for the team) |
| NFR-14 | Built within 24 h window | p9, RULES.md §1 | — | Manual (git history) | CONFIRMED | TODO (repository not yet committed) |
| SUB-01 | Repo: code, config example, deps, attributions | p9 | repo root | Manual | CONFIRMED | DONE |
| SUB-02 | README contents | p9 | `README.md` | Manual | CONFIRMED | DONE |
| SUB-03 | Runnable local demo | p9 | `app.py`, `README.md` | Manual | CONFIRMED | DONE (local) |
| SUB-04 | Architecture diagram matches implementation | p9, p10 | `docs/ARCHITECTURE.md` | Manual | CONFIRMED | DONE (diagram regenerated from module layout 2026-10-10) |
| SUB-05 | Evidence: opening log, post-event log, final plan files | p9, p10 | Separate dashboard exports | Manual | CONFIRMED | PARTIAL (saved prior Gemini smoke database is local/ignored; final exported evidence is not packaged in the source release) |
| SUB-06 | Demo video 3–5 min | p9, RULES.md §6 | — | Manual | CONFIRMED | TODO (team) |
| SUB-07 | Team declaration | p9 | Submission form | Manual | CONFIRMED | TODO (lead/member identities, contact, and signature) |
| SUB-08 | GitHub repo link submitted | RULES.md §6 | — | Manual | CONFIRMED | TODO (team) |

## Scenario fixtures for tests (verified by orchestrator enumeration)

- Baseline 79/52/59/26/17: exactly 2 valid plans — A `L3+M2+F2+E2` (P79/W50/O57/R26/B17, risk 24), B `L2+M2+F2+E3`
  (P79/W50/O59/R26/B16, risk 24).
- Each practice event: 0 plans with baseline policy; with override: Power-4: 3, Water-3: 5, Oxygen-3: 2, Robot-4: 1
  (`L3+M2+F2+E3`, risk 28), Bandwidth-2: 3. Full lists in `PRD.md` Appendix A.

