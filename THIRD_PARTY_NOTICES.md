# Third-party material and licence scope

> Built for the Loopverse Challenge, designed by LIFIX Labs.
> Original challenge repository: <https://github.com/lifix-prog/Loopverse-AI_ML_Virtual_Module>

## What the MIT License covers

The MIT License in [`LICENSE`](LICENSE) covers only **original ARES Accord work**: the code, tests, scripts,
configuration, diagrams and documentation written for this project, either by Muhammad Dayan or by contributors
under the Contributing terms in the README.

It does **not** cover:
- the challenge materials listed below;
- third-party components.

These remain under their owners' terms, and this repository's licence does not override them.

## Organiser permission

On 2026-11-09, the organiser of the Loopverse challenge (owner of the original challenge repository) confirmed in
writing to Muhammad Dayan that:
1. the code he wrote himself for the challenge is his original work, and he may claim authorship of it;
2. he may publish and maintain it in a repository under his own GitHub account;
3. he may apply the MIT License to his own code, with the challenge material files and third-party material kept
   outside it, under their own terms;
4. the challenge concept, rules and starter materials remain credited to the organisers. The README must carry the
   line "Built for the Loopverse Challenge, designed by LIFIX Labs.", and their repository should be linked rather
   than copied.

The author keeps the original message.

## Challenge material (not covered by the MIT License)

The original challenge materials (the participant guide, rules, starter files and sample files) are **not
redistributed** here; get them from the original challenge repository linked above. That repository publishes no
licence, so all rights in this material remain with its owners.

The project does contain the following material taken from them. It is included only so the simulator and its
tests reproduce the challenge.

| Material | Where it appears | Origin |
|---|---|---|
| Official sample crisis event, verbatim | `tests/fixtures/event_injection_format.json`; `_STARTER_EVENT_FALLBACK` in `ares/ui/controls.py` | `event_injection_format.json` in the challenge repository |
| The 12 mode packages (resource figures, risk scores and consequence texts, verbatim) | `MODE_PACKAGES` in `ares/models.py` | Participant guide, p. 4 |
| Scenario parameters: the starting resource pool, risk and Sacrifice limits, required return agreements, and the 3-minute replanning window | `BASELINE_POOL`, `Policy` limits and `RETURN_TEMPLATES` in `ares/models.py`; the deadline default in `ares/config.py` | Participant guide, pp. 4, 6, 8 and 10 (page references from `PRD.md`) |
| Colony scenario and agent profiles (roles, goals, constraints) | `ares/agents/profiles.py` | Derived from the participant guide, p. 3 |
| Baseline crisis name and description | `BASELINE_NAME` and `BASELINE_DESCRIPTION` in `ares/orchestrator.py` | Derived from the participant guide, p. 3 |
| Human co-sign threshold (risk > 20) | `COSIGN_RISK_THRESHOLD` in `ares/orchestrator.py`; the co-sign help text in `ares/ui/controls.py`; `flag_human_review` in `ares/exports.py` | The starter kit's submission checklist (bonus item) |
| Practice events (resource changes and expected outcomes) | `PRACTICE_EVENTS` in `tests/test_acceptance.py` and `tests/test_optimizer.py` | Participant guide, p. 8 |
| Baseline name and description, consequence texts and return-agreement texts, repeated in generated output | `docs/demo/rule-based-sample/*` (output of this project's own demo run); the dashboard screenshots in `docs/screenshots/*.png` | As above |
| Summaries of, restatements of, and quotations from the challenge rules and guide | `PRD.md`, `docs/CONTRACT.md`, `docs/DECISIONS_AND_RISKS.md`, `docs/REQUIREMENTS_TRACEABILITY.md`; a short scenario summary in `README.md` | Participant guide; starter files |
| Organiser identifiers asserted in tests | `tests/test_events.py`, `tests/test_acceptance.py` | The challenge's sample event and guide |

Notes:
- `tests/fixtures/output_schema.json` is an **original reconstruction**. It only follows the organisers' field
  names. See `tests/fixtures/README.md`.
- "ARES Accord", "Loopverse", "LIFIX Labs" and "Looplab" are used only to identify the challenge and its
  organisers. The original submission credited "Looplab / LifixLabs".
- **If you reuse this repository,** you may use the original work under the MIT License. Remove or replace the
  challenge material above, unless you have the organisers' permission.

## Third-party software (not bundled)

No third-party code, fonts, images or templates are bundled in this repository. Dependencies are installed from
their own distribution channels under their own licences.

| Component | Source | Licence (verified from) | How it is used |
|---|---|---|---|
| Streamlit 1.67.0 | <https://github.com/streamlit/streamlit> | Apache-2.0 (installed package metadata) | Runtime dependency, `requirements.txt` |
| pandas 3.1.1 | <https://github.com/pandas-dev/pandas> | BSD-3-Clause (installed package metadata) | Runtime dependency |
| requests 2.34.2 | <https://github.com/psf/requests> | Apache-2.0 (installed package metadata) | Runtime dependency |
| python-dotenv 1.2.4 | <https://github.com/theskumar/python-dotenv> | BSD-3-Clause (installed package metadata) | Runtime dependency |
| pytest 9.2.0 | <https://github.com/pytest-dev/pytest> | MIT (installed package metadata) | Development dependency, `requirements-dev.txt` |
| pytest-cov 7.1.0 | <https://github.com/pytest-dev/pytest-cov> | MIT (installed package metadata) | Development dependency |
| ruff 0.17.4 | <https://github.com/astral-sh/ruff> | MIT (installed package metadata) | Development dependency |
| matplotlib (unpinned) | <https://github.com/matplotlib/matplotlib> | Matplotlib License, PSF-based ("License agreement for matplotlib versions 1.3.0 and later", from its LICENSE file) | Optional; only for `scripts/make_architecture_png.py`. It is not in either requirements file. The generated `docs/architecture_diagram.png` is original work. |
| Transitive dependencies (for example numpy, altair, pyarrow) | PyPI | **Not individually verified.** Review before redistributing a bundled build. | Installed automatically |
| `actions/checkout@v7` | <https://github.com/actions/checkout> | MIT (repository licence) | Referenced by the CI workflow |
| `conda-incubator/setup-miniconda@v4` | <https://github.com/conda-incubator/setup-miniconda> | MIT (repository licence) | Referenced by the CI workflow |
| `actions/setup-python@v7` | <https://github.com/actions/setup-python> | MIT (repository licence) | Referenced by the CI workflow |
| Python and the conda packages in `environment.yml` | python.org / conda-forge | Their own licences (not reviewed here) | Installed by the user or CI |
| Gemini, Groq, OpenRouter and Ollama APIs | Each provider | Each provider's terms of service | Optional; called with the user's own keys |

The licence text in `LICENSE` is the standard MIT License template.
