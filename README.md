# ARES Accord — Multi-Agent Resource Negotiation for a Mars Colony Emergency

> **Verification (2026-10-10):** the current automated suite passes **258 tests** (63.55 seconds; see
> [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md)). Previously saved smoke evidence records a Gemini-backed baseline and
> Solar aftershock recovery, both approved in round 4 with no rule-based fallback messages. Those are prior local-run
> results, not a fresh live run performed for this README update. Automated tests do not prove current API availability.

Five agents — a **Commander** and four departments (**Life Support, Medical, Food Production, Engineering**) — negotiate
over one shared pool of Power, Water, Oxygen, Robot time and Bandwidth. Each department must pick exactly one of its
three fixed mode packages (Standard / Restricted / Sacrifice). The pool is too small for everyone, so one department
must accept a Sacrifice and receive two accepted return commitments from different agents. A **deterministic validator
(not an LLM)** checks every numbered plan version; the Commander can approve only after a validator PASS and four ACCEPT
votes on the same current version, after at least three rounds. Judges then inject a crisis from the dashboard and the
council renegotiates (Crisis Override allowed after an event) to a new approved plan or a justified INFEASIBLE result.

Built for the Looplab × LifixLabs **ARES Accord** 24-hour AI/ML hackathon (Participant Guide v2.1 and the
[official starter repository](https://github.com/lifix-prog/Loopverse-AI_ML_Virtual_Module)). Organizer materials are
not bundled pending redistribution permission. Requirements: [`PRD.md`](PRD.md).

## Quick start (local, $0)

Requires Python 3.13 (3.11+ likely works _(unverified)_).

Windows (PowerShell, from the repo root; in Git Bash use `cp` instead of `copy`):
```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt
copy .env.example .env        # optional: only needed for an LLM provider
.venv/Scripts/python -m streamlit run app.py
```
macOS / Linux:
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env          # optional
.venv/bin/python -m streamlit run app.py
```
Open http://localhost:8501. With no keys the app runs in **rule-based** mode (every turn labelled as fallback/demo).
Headless smoke run (baseline + Power -4 event):
```powershell
.venv/Scripts/python scripts/smoke_run.py --provider rule-based --db data/smoke-demo.db
```
Choose a new database path: the script appends run history to its selected database.
One-click scripts: `scripts/run.ps1` / `scripts/run.sh` (create venv, install, copy `.env`, launch).

## Architecture (summary)

```text
app.py (Streamlit) ──> ares/service.py ──> ares/orchestrator.py ──┬─> ares/agents/  (Commander + 4 departments,
                                                                  │      private profile/state/memory)
                                                                  │      └─> ares/llm/ (Gemini, Groq, OpenRouter, Ollama)
                                                                  ├─> ares/validator.py (deterministic checks + approval gate)
                                                                  ├─> ares/optimizer.py (candidates + infeasibility; never approves)
                                                                  ├─> ares/events.py    (event parsing / application)
                                                                  └─> ares/storage.py   (SQLite data/ares.db)
ares/exports.py ── JSON / CSV / TXT bundle        ares/models.py ── contract (12 immutable packages, policies, templates)
```
Details, Mermaid component + sequence diagrams: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Interface contract:
[`docs/CONTRACT.md`](docs/CONTRACT.md). Spec conflicts and assumptions: [`docs/DECISIONS_AND_RISKS.md`](docs/DECISIONS_AND_RISKS.md).

Key rules enforced in code (not by the LLM): 12 fixed packages (PDF p4); baseline risk <= 24 and <= 1 Sacrifice;
>= 2 ACCEPTED returns from different agents per sacrificing department; plan versions numbered per session; any plan
change clears votes; approval = PASS + 4 ACCEPT on current version + >= 3 rounds; Crisis Override (risk 28, 2
Sacrifices) only after an event.

## LLM providers and environment variables

Copy `.env.example` to `.env` and fill **only** the provider you use. `.env` is git-ignored. Default model ids: see
`ares/llm/providers.py`.

| Provider (`ARES_PROVIDER`) | Variables | Cost / notes |
|---|---|---|
| `rule-based` (default) | — | No LLM. Deterministic policy, every message labelled fallback/demo. Not used for the demo video. |
| `gemini` | `GEMINI_API_KEY`, `GEMINI_MODEL` | Google AI Studio free tier (rate limited). |
| `groq` | `GROQ_API_KEY`, `GROQ_MODEL` | Groq free tier (tokens-per-minute limits). |
| `openrouter` | `OPENROUTER_API_KEY`, `OPENROUTER_MODEL` | Use a model id ending in `:free` (daily caps). |
| `ollama` | `OLLAMA_BASE_URL` (default `http://localhost:11434`), `OLLAMA_MODEL` | Local, no key; needs Ollama installed. |

| Limit variable | Default | Meaning |
|---|---|---|
| `ARES_LLM_TIMEOUT_S` | 25 | Per-request timeout |
| `ARES_LLM_MAX_RETRIES` | 2 | Bounded retries before fallback |
| `ARES_LLM_MIN_INTERVAL_S` | 0 | Pacing between calls (raise for free-tier RPM limits) |
| `ARES_SCENARIO_DEADLINE_S` | 170 | Post-event deadline (PDF: 3 minutes) -> TIMEOUT |
| `ARES_DB_PATH` | `data/ares.db` | SQLite history |

If a call fails, times out, or returns invalid JSON (after one repair retry), that agent's **turn** falls back to the
rule-based policy and is shown with a fallback badge and reason. Fallback is never presented as LLM output.

## Running a demo

1. **Judge Controls** (sidebar): check/edit the pool (default 79/52/59/26/17), choose a provider, max rounds, click **Start**.
2. Watch **Council Transcript**: Commander briefing -> Round 1 proposals (everyone requests Standard) -> conflicts ->
   objections, sacrifice refusals, counteroffers, return commitments -> numbered plan versions -> validation -> votes.
3. **Validation & Votes** shows PASS/FAIL per check with reasons; approval appears only after PASS + 4 ACCEPT on the
   current version, after round 3. **Mode Board** shows each department's package, consequence, risk and commitments;
   **Mission Control** shows pool, used, reserve, round, plan version and status.
4. **Inject event**: paste the official event JSON format, type text such as
   "Available Robot time decreases by 4", or use the form. Preview shows the interpreted change before applying.
5. Recovery: old plan marked **STALE**, commitments reviewed, Crisis Override may be enabled, >= 2 rounds, fresh votes ->
   new APPROVED version, or **INFEASIBLE** with blocking constraints and the needed change (e.g. "+4 Power").
6. **Export** (Judge Controls): download the bundle.

## Exports

**Prepare export files** in Judge Controls, then download each file. The bundle covers the current session; the
full multi-session history is in `full_history_all_sessions.csv`.

| File | Content |
|---|---|
| `final_allocation.json` | One record per agent shaped to the official starter schema (+ PDF resources, `vote_pdf`, `flag_human_review = risk > 20`). See D3/D4. |
| `final_plan.json` | Approved plan: modes, packages, commitments, totals, reserve, risk, policy, validation, votes, decision. |
| `council_transcript.json` / `.csv` / `.txt` | Full persistent transcript of all scenarios (agent, type, round, plan version, source). |
| `crisis_handling_log.txt` | Event, interpretation, STALE plan, commitment review, override, rounds, outcome. |
| `plans.csv` | Every plan version: modes, used/reserve per resource, validation status, commitments. |
| `full_history_all_sessions.csv` | Transcript of every session and scenario ever run on this machine. |

## Tests

```powershell
.venv/Scripts/python -m pytest -q        # offline; no network or keys needed
```
Suites: `tests/test_validator.py`, `test_optimizer.py`, `test_events.py`, `test_llm.py`, `test_agents.py`,
`test_orchestrator.py`, `test_orchestrator_core.py`, `test_live_fix.py`, and `test_acceptance.py`.
Latest run (2026-10-10, existing project virtual environment): **258 passed in 63.55s**. This is an automated test
run, not a live Gemini API test or a complete manual browser audit. See [`docs/TEST_REPORT.md`](docs/TEST_REPORT.md)
and [`docs/REQUIREMENTS_TRACEABILITY.md`](docs/REQUIREMENTS_TRACEABILITY.md).

## Verifying a live LLM run

1. Get a provider key (Gemini: https://aistudio.google.com/apikey · Groq: https://console.groq.com/keys) and put it in `.env`:
   ```dotenv
   GEMINI_API_KEY=...
   # or
   GROQ_API_KEY=...
   ```
   Never commit `.env`.
2. Headless:
   ```powershell
   .venv/Scripts/python scripts/smoke_run.py --provider gemini --db data/gemini-smoke-demo.db
   ```
   (or `groq`).
   Choose a new database path to preserve other smoke evidence. Messages tagged `[llm/<provider>]` are model output;
   `[rule-based]` marks a labelled per-turn fallback.
3. Dashboard: pick the provider in Judge Controls (✅ = key found) and press **Start baseline crisis**.

Provider quotas and model availability depend on the provider account and may change. A previous saved Gemini
smoke run used `gemini-3.5-flash-lite`; choose Gemini in Judge Controls for a live run. Per-provider defaults are in
`ares/llm/providers.py`.

## Reset

- **Reset session** (Judge Controls): starts a new session; history kept.
- **Wipe history**: deletes all stored sessions/messages (`data/ares.db` contents).
- Manual: stop Streamlit and delete `data/ares.db`.

## Known limitations

- No hosted demo ($0 budget): run locally.
- Free-tier LLM quotas/rate limits can force labelled rule-based fallback mid-run; slow models may hit the 170 s
  post-event TIMEOUT.
- LLM agents may fail to converge -> DEADLOCK after max rounds (a valid, reported outcome).
- Streamlit runs a negotiation synchronously; the UI updates via live callbacks but other controls wait until the run ends.
- Event durations (`duration_hours`) are recorded, not simulated; percent reductions are rounded up (conservative).
- Human-in-the-loop (bonus): optional Judge Controls toggle; when on, plans with risk > 20 that passed the gate wait
  for the judge to **Co-sign** or **Veto** (default off, because both valid baseline plans have risk 24). Records always
  carry `flag_human_review = risk > 20` (D8).
- Architecture diagram: [`docs/architecture_diagram.png`](docs/architecture_diagram.png) (regenerate with
  `scripts/make_architecture_png.py`, needs matplotlib) and Mermaid source in `docs/ARCHITECTURE.md`.

## Attributions and declarations

- Python 3.13, [Streamlit](https://streamlit.io) (Apache-2.0), [pandas](https://pandas.pydata.org) (BSD-3),
  [requests](https://requests.readthedocs.io) (Apache-2.0), [python-dotenv](https://github.com/theskumar/python-dotenv)
  (BSD-3), [pytest](https://pytest.org) (MIT); SQLite via Python stdlib.
- LLM APIs (optional, user-supplied keys): Google Gemini API, Groq API, OpenRouter, Ollama (local). Model ids: `ares/llm/providers.py`.
- Challenge material: ARES Accord Participant Guide v2.1 and starter kit by Looplab / LifixLabs (official source linked above; not bundled pending redistribution permission).
- **AI coding assistant:** Claude Code (Anthropic) was used during development for code, tests and documentation.
- Team member names, contact details, and signed declarations are supplied through the submission form, not this source release.
- Generated run exports and local smoke databases are excluded; capture and submit genuine dashboard exports separately.
