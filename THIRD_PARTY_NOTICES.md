# Third-party material and licence scope

The MIT License in [`LICENSE`](LICENSE) covers the original work in this repository by MUHAMMAD DAYAN: the code,
tests, scripts, diagrams and documentation written for this project.

It does **not** cover the material listed below. That material comes from the organisers of the Looplab ×
LifixLabs ARES Accord hackathon: their
[starter repository](https://github.com/lifix-prog/Loopverse-AI_ML_Virtual_Module) and Participant Guide.
- **No licence.** Neither publishes a licence, so all rights in this material remain with its owners.
- **Why it's included.** It is here only so the simulator and its tests reproduce the official challenge.

| Material | Where it appears | Origin |
|---|---|---|
| Official sample crisis event (verbatim) | `tests/fixtures/event_injection_format.json`; `_STARTER_EVENT_FALLBACK` in `ares/ui/controls.py` | `event_injection_format.json` in the starter repository |
| The 12 mode packages: resource figures, risk scores and consequence texts (verbatim) | `MODE_PACKAGES` in `ares/models.py` | Participant Guide v2.1, p4 |
| Colony scenario and agent profiles (roles, goals, constraints) | `ares/agents/profiles.py` | Derived from Participant Guide v2.1, p3 |
| Text quoted from the Participant Guide | `PRD.md`, `docs/DECISIONS_AND_RISKS.md`, `docs/REQUIREMENTS_TRACEABILITY.md` (these documents summarise and cite the guide by page) | Participant Guide v2.1 |

## Notes

- **The reconstructed schema is original.** `tests/fixtures/output_schema.json` was written for this project and
  only follows the organisers' field names. See `tests/fixtures/README.md`.
- **Names.** "ARES Accord", "Looplab" and "LifixLabs" are used only to identify the hackathon.
- **If you reuse this repository,** remove or replace the material above unless you have the organisers'
  permission.
- **Dependencies.** Third-party packages (Streamlit, pandas, requests, python-dotenv, pytest, pytest-cov, ruff) are
  not bundled. They are installed from PyPI under their own licences, which are listed in the README.
