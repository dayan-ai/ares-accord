# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Test numbers are measured runs (Windows 11, Python 3.13.6, fully offline) unless stated otherwise.

## [Unreleased] — ownership, attribution and licence scope (branch `docs/ownership-licensing`)

Documentation and licensing only; no behaviour change. This follows the organiser's written permission of
2026-11-09.
- **Attribution.** The README carries the organiser-requested line "Built for the Loopverse Challenge, designed by
  LIFIX Labs." It links the original challenge repository, states individual authorship (Muhammad Dayan) and the
  2nd-place (Runner-up) result, and gains Contributing and Author sections.
- **`LICENSE`.** The standard MIT text is unchanged. The copyright lines now say that the licence covers only
  original ARES Accord work, meaning Muhammad Dayan's work and contributions accepted under the licence. The
  challenge materials and third-party components stay with their owners. The README and
  `THIRD_PARTY_NOTICES.md` state the same scope.
- **`THIRD_PARTY_NOTICES.md`.** It now summarises the organiser's permission, and lists every place where challenge
  material appears. That adds these to the earlier list:
  - the scenario parameters;
  - the baseline crisis text and the co-sign threshold;
  - the practice events in the tests;
  - the challenge rules restated in `docs/CONTRACT.md`;
  - the demo bundle and the README summary.

  It also lists each dependency, GitHub Action and the optional matplotlib with its verified licence.
- **File-level notes.** The `ares/models.py` docstring now names every challenge-derived constant, and
  `docs/demo/README.md` notes the challenge text in the generated bundle.

## [Unreleased] — `ARES_LOG_LEVEL` from `.env` (merged into `main` as `e7cbecf`)

- **Fix.** An `ARES_LOG_LEVEL` set only in `.env` was ignored on the dashboard's first script run.
  - **Cause:** `app.py` configures logging before anything imports `ares.config`, which is what loads `.env`. The
    value applied only from the next rerun.
  - **Change:** `configure_logging()` now imports `ares.config` first.
  - **Test:** `test_configure_logging_applies_level_from_dotenv_on_first_call` runs in a fresh interpreter with a
    stub `dotenv`, so no real `.env` is touched.
  - **Docs:** the first-rerun caveat is removed from the README and `.env.example`.
- **Results:** ruff clean, **313 passed** (312 plus the new test), 88% coverage, mypy unchanged at 96 errors
  (`ares/log.py` is clean). A manual check with a real `.env` in a scratch clone shows the first run now logs at
  the configured level.

## [Unreleased] — post-merge documentation audit (merged into `main` as `cc5e79f`)

Documentation and licensing only; no behaviour changed. Three source files gained a comment or docstring line.
Results re-measured on merged `main` (`c3fa02a`), 2026-11-09:
312 passed, 88% coverage, ruff clean, mypy 96 errors. GitHub Actions on `main` passes (Python 3.12). Clean
clones on Windows pass on Python 3.12.14 and 3.13.6.

- **README results and CI.** The results table was re-measured on `main`. The CI badge is pinned to `main` and
  linked to the workflow. The Python badge and Quick start now say that CI runs 3.12 only and 3.13 is verified
  locally.
- **Organiser material.** The README, `PRD.md` and `tests/fixtures/README.md` said organiser materials were
  not bundled. In fact the scenario, the mode-package table and the official sample event are reproduced, and the
  organisers' starter repository publishes no licence. The README has a new "Licence and third-party material"
  section.
- **Settings wording.** The README and `.env.example` now say exactly which invalid numeric settings fall back to
  the default and which are clamped. `ARES_LOG_LEVEL` was added to `.env.example`. A value set only in `.env`
  reaches the dashboard from its first rerun, not at startup.
- **Licence.** The original work is now released under the MIT License (`LICENSE`, © 2026 MUHAMMAD DAYAN).
  `THIRD_PARTY_NOTICES.md` lists the organiser material that the licence does not cover. A note pointing to it was
  added at each place that material appears: `ares/models.py`, `ares/agents/profiles.py`, `ares/ui/controls.py`
  and `tests/fixtures/README.md`.
- **Smaller fixes.** The labels of the reset and wipe controls are corrected. The README notes that mypy is
  installed separately, and lists pytest-cov among the third-party packages.

## [Unreleased] — post-hackathon hardening (branch `portfolio/harden-ares-accord`, merged into `main` as `c3fa02a`)

Starting point: `562dce3` (hackathon submission). **Before:** 255 passed, 3 failed, 62 warnings, 73% coverage, a
CI workflow that could not run, unpinned dependencies. **After:** 312 passed, 0 failed, 0 warnings, 88% coverage,
ruff clean, pinned dependencies, and a corrected CI workflow (validated with actionlint; passing on GitHub
Actions with Python 3.12: ruff clean, 312 passed, 88% coverage).

### Hostile-input findings — event-input limits and traceback redaction

These were found by adversarial probing after the documentation pass. Each has a regression test written
before the fix.

#### Fixed
- **Availability.** Free-text event parsing is super-linear in the number of number/resource mentions per
  sentence (measured: 22 kB → 9 s, 66 kB → 73 s), so a single pasted event could block the single-process app.
  Event input over 5,000 characters (`MAX_EVENT_INPUT_CHARS`, raw text or serialised JSON) is now rejected
  before parsing, with a clear message. At the limit, the worst case measured 0.59–0.62 s (3 runs, dev
  machine); the test allows 5 s.
- **Nesting.** Deeply nested event input raised `RecursionError`, contradicting `parse_event`'s documented
  contract. It now raises `ValueError`.
- **Scientific notation.** Numeric strings such as `"1e999"` were read as their leading digit (+1 Power,
  silently). Exponents are now part of the token: `"1e3"` is 1000, and `"1e999"` is out of range and ignored
  with a note.
- **Tracebacks.** Log redaction covered messages but not tracebacks (`exc_info`) or stack info, so a key inside
  an exception message could reach the log. Both are now redacted.

### Test coverage of documented behaviour

#### Added
- Circuit-breaker tests: the breaker opens after `CIRCUIT_FAILURES` consecutive failures, fails fast without
  making an HTTP request, and resets after a success. The README advertised the breaker, but no test covered it.

### Security and correctness — `370fb5c`

#### Fixed
- Judge events containing extremely large numbers (for example a 400-digit value) crashed `parse_event` /
  `apply_event` with `OverflowError`. Values that are non-finite, or beyond ±1,000,000, are now ignored with an
  interpretation note. Durations are bounded too.
- Numeric environment settings accepted `nan`, `inf` and negative values. A `nan` scenario deadline never
  expired. Invalid values now fall back to defaults with a logged warning.
- Provider and model names were rendered inside `unsafe_allow_html` without HTML escaping.

#### Added
- Prompt-injection hygiene: judge and agent free text in prompts is wrapped in `[[ ]]`, and all four system
  prompts state that delimited text is data, not instructions.
- Defence in depth for SQLite: dynamic `UPDATE` column names are validated against the schema.
  `Store.close()` and context-manager support.
- `.env.example` now documents every variable the code reads.
- `tests/test_hardening.py` (32 tests). It includes an exhaustive property check: for all 81 mode
  combinations × 2 policies, the approval gate approves exactly what the validator passes, and every approval
  satisfies an independent oracle of the hard limits. Inspection found no bypass of the gate; the tests lock
  that in.

### Build and reproducibility — `e0d6287`

#### Fixed
- **CI.** The workflow called `conda env update --file environment.yml`, but the file did not exist, and it used
  `actions/setup-python@v3`, which actionlint reports as too old to run. It is rewritten around
  `conda-incubator/setup-miniconda` with a new `environment.yml`, pinned dev requirements, `ruff check .` and
  `pytest --cov`.
- **Dependencies.** The `streamlit>=1.40` floor could not run the app (`st.tabs(key=, on_change=)`). Runtime
  dependencies are now pinned exactly in `requirements.txt`, and tooling in `requirements-dev.txt`. pytest is no
  longer a runtime dependency.
- **Clean clones.** Three tests read the gitignored organiser `starter_kit/`, so clean clones failed.
  `tests/_paths.starter_file()` now falls back to `tests/fixtures/` (a verbatim event copy plus a schema
  reconstructed from documentation; provenance in `tests/fixtures/README.md`). No assertion changed.

#### Changed
- Added `ruff.toml` (E, F, W, B; 120 columns) and fixed the 32 existing findings: unused imports, long lines,
  `zip(strict=True)`, `raise … from`.

### Testing — `2ad64aa`

#### Added
- `tests/test_ui_smoke.py`: renders the real Streamlit app headlessly (`AppTest`) and drives a rule-based run
  to an APPROVED banner. The UI modules previously had 0% coverage.

#### Fixed
- Tests leaked SQLite connections (62 `ResourceWarning`s). An autouse fixture in `tests/conftest.py` now closes
  every store.

### Observability, safety and demo — `2d822af`

#### Added
- `ares/log.py`: idempotent `key=value` logging (`ARES_LOG_LEVEL`) and `redact_secrets()`. It masks configured
  `*_API_KEY` / `*_TOKEN` / `*_SECRET` values and key-shaped tokens, and is applied to every `ares.*` log
  record through a handler filter.
- Orchestrator logs scenario start and end, every approval-gate decision, and real LLM fallbacks. The three
  silent `except: pass` blocks now log warnings; behaviour is unchanged.
- LLM client logs HTTP errors, retries and final failures by provider and status, never headers or prompts.
- `docs/ARCHITECTURE.md` §5a: trust-boundary flowchart and plan-lifecycle state diagram.
- `docs/demo/`: a reproducible rule-based export bundle and guide.

#### Fixed
- `short_error()` passed provider error text, shown in the UI and logged, through unchanged. It is now redacted
  before truncation, in case a provider echoes a key.
- `scripts/smoke_run.py` deletes the database given with `--db`, and the README wrongly said it appended. It now
  refuses the dashboard database, and the documentation is corrected.

### Documentation — this commit

#### Changed
- README rewritten:
  - problem statement and actual capabilities;
  - architecture, agent workflow, and how the approval gate works;
  - setup, every environment variable, the reproducible demo, measured test results;
  - security, honest limitations, roadmap.
- `docs/SECURITY.md`: post-hackathon hardening table.
- `docs/TEST_REPORT.md` and `docs/REQUIREMENTS_TRACEABILITY.md` marked as hackathon-time snapshots.

### Known gaps (not addressed)
- 96 mypy errors, mostly `Optional` narrowing. Count unchanged from the baseline; not a CI gate.
- CI runs on Python 3.12 only. Python 3.13 is verified by local clean-clone runs, not by CI.
- Transitive dependencies are not locked. The minimum compatible Streamlit version is unknown (1.67.0 was
  tested).
- No evaluation harness for LLM negotiation quality.

## [Hackathon submission] — 2026-10-10 (`7486aac`, `be6e835`, `562dce3`)

Built in 24 hours for the Looplab × LifixLabs ARES Accord hackathon:
- the Streamlit dashboard;
- five agents and four LLM providers, with labelled rule-based fallback;
- the deterministic validator and approval gate;
- event parsing, Crisis Override, the INFEASIBLE diagnosis;
- the fact-check and reconsider step;
- exports;
- 258 tests at submission time.

See `docs/TEST_REPORT.md` and `docs/REQUIREMENTS_TRACEABILITY.md` (historical snapshots).
