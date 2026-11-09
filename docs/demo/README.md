# Reproducible demo output

`rule-based-sample/` is the complete export bundle from one headless run with the **deterministic rule-based
provider** (no LLM, no API key): baseline crisis, then the default event *"Available Power decreases by 4"*.

| | Result (see `plans.csv`) |
|---|---|
| Baseline | v1 `L1+M1+F1+E1` FAIL → v2 `L2+M2+F2+E2` FAIL → **v3 `L3+M2+F2+E2` PASS, APPROVED in round 3** (risk 24) |
| Event (Power 79 → 75) | v3 marked STALE → v4, v5 FAIL → Crisis Override → **v6 `L3+M2+F3+E2` APPROVED in round 3** (risk 28, two Sacrifices, four return commitments) |
| Message sources | `system`, `validator` and `rule-based` only. Every agent turn is labelled `RULE-BASED FALLBACK`. |

Regenerate it (identical content except timestamps and the generated event id):

```bash
python scripts/smoke_run.py --provider rule-based --db data/demo.db --export docs/demo/rule-based-sample --quiet
```

This sample shows the protocol, the validator and the exports working end to end. It is **not** evidence of
LLM negotiation quality. For an LLM run, set a provider key in `.env` and pass `--provider gemini|groq|openrouter`;
those messages are tagged `[llm/<provider>]`.

| File | What to look at |
|---|---|
| `crisis_handling_log.txt` | Event interpretation, STALE plan, commitment review, outcome |
| `council_transcript.txt` | The full negotiation, round by round |
| `final_plan.json` | Approved modes, totals, reserve, risk, validation checks, votes |
| `final_allocation.json` | One record per agent in the starter-kit output shape |
| `plans.csv` | Every plan version and its validation status |

**Ownership.** These files are generated output of this project. They repeat the Loopverse challenge's
mode-package consequence texts and return-agreement texts (designed by LIFIX Labs). That challenge text is not
covered by this repository's MIT License; see [`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md).
