# Test fixtures

The hackathon organisers' `starter_kit/` folder is not redistributed (it is gitignored). Three tests need two
of its files, so a clean clone carries stand-ins here. Tests use `tests/_paths.py::starter_file()`, which
**prefers `starter_kit/<name>` when it exists** and falls back to this folder otherwise.

| File | Provenance |
|---|---|
| `event_injection_format.json` | Verbatim copy of the official example event already embedded in the app (`ares/ui/controls.py`, `_STARTER_EVENT_FALLBACK`). |
| `output_schema.json` | **Reconstructed**, not the organisers' file. Its top-level keys come from the schema description in `docs/DECISIONS_AND_RISKS.md` (decision D3): `round, agent_id, proposal{…}, vote, risk_score, flag_human_review`. The tests only check that every exported record contains these top-level keys. |

If you have the original `starter_kit/`, put it in the repository root and the tests will use it instead.
