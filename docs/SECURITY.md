# Security & operations notes

Checked 2026-10-10 (commands below are re-runnable).

| Item | Status | How verified |
|---|---|---|
| Secrets only via env vars / `.env` | OK | `ares/llm/providers.py` reads `*_API_KEY` from the environment; `.env` is git-ignored (`git status --ignored`) |
| `.env.example` contains names only | OK | file review |
| No secrets committed | OK | `grep -rnE "(AIza[0-9A-Za-z_-]{20,}\|gsk_[0-9A-Za-z]{20,}\|sk-or-[0-9A-Za-z-]{20,})" --exclude-dir=.venv .` -> no match |
| Model output treated as data | OK | parsed with `json` only (`ares/llm/jsonutil.py`), then sanitised (`ares/agents/sanitize.py`); no `eval`/`exec`/`subprocess`/`shell=True` anywhere (`grep`) |
| Every HTTP call has a timeout | OK | `requests.post(..., timeout=(connect, read))` in `ares/llm/base.py`; Ollama probe 1 s |
| Bounded retries | OK | `ARES_LLM_MAX_RETRIES` (default 2) with backoff, Retry-After capped at 20 s, all inside the per-call budget; 3 consecutive failures pause a provider for 45 s |
| Scenario wall-clock limit | OK | `ARES_SCENARIO_DEADLINE_S` (170 s after an event); LLM turns stop 20 s earlier and fall back to labelled rule-based turns |
| Approval cannot be forged by a model | OK | approval only via `validator.approval_gate` (validator PASS + 4 ACCEPT on current version + min rounds); agents have no approve field |
| Local-first, $0 | OK | runs fully offline in rule-based mode; LLM providers are free tiers (keys supplied by the user) or local Ollama |
| Data at rest | Note | `data/ares.db` (SQLite) holds transcripts only, no credentials; delete with Wipe history / `scripts/reset.*` |
| Paid model guard | OK | OpenRouter accepts only `:free` ids or `openrouter/free` unless `OPENROUTER_ALLOW_PAID=1` |

Before submission: run the secret grep above and `git status --ignored` and confirm `.env` and `data/*.db` are ignored.

## Post-hackathon hardening (2026-11)

Each item has regression tests in `tests/test_hardening.py` or `tests/test_observability.py`.

| Item | Change | Test |
|---|---|---|
| Approval-gate bypass | Inspection found no path to `APPROVED` except through `approval_gate` (plus the judge co-sign, which only acts after the gate). Locked in with an exhaustive check of all 81 combinations × 2 policies against an independent oracle, and a single-precondition mutation matrix. | `test_gate_*` |
| Prompt injection | Untrusted text (event name and description, transcript, memory, custom-offer promises, original vote reasons) is wrapped in `[[ ]]`, with delimiters inside it neutralised. All four system prompts state that delimited text is data. This is defence in depth only; the guarantee remains the sanitizer plus the gate. | `test_untrusted_text_is_delimited_in_every_prompt` |
| Hostile or malformed event input | Non-finite values, or values beyond ±1,000,000, are ignored with an interpretation note. They previously raised `OverflowError`. | `test_out_of_range_*` |
| Config values | `nan`, `inf` or negative numeric env settings fall back to defaults with a warning. A NaN deadline previously never expired. | `test_invalid_deadline_env_*`, `test_non_finite_llm_env_values_fall_back` |
| Secrets in logs and errors | `ares.log.redact_secrets` masks configured `*_API_KEY` / `*_TOKEN` / `*_SECRET` values and key-shaped tokens. It is applied to provider error text (`short_error`) and, through a handler filter, to every `ares.*` log record. | `test_redact_*`, `test_short_error_*`, `test_log_handler_*` |
| SQL | Values are parameterised. Dynamic `UPDATE` column names are validated against `PRAGMA table_info`. | `test_store_rejects_unknown_or_injected_column_names` |
| HTML output | Provider and model names inside `unsafe_allow_html` are now escaped; all other dynamic values already were. | covered by `test_ui_smoke.py` rendering |
| Destructive script | `scripts/smoke_run.py` deletes its `--db` file, so it now refuses the dashboard database. | `test_smoke_run_refuses_to_delete_the_dashboard_database` |
| Oversized event input (availability) | Found by hostile-input probing: free-text parsing is super-linear (22 kB → 9 s, 66 kB → 73 s), so one pasted event could block the app for minutes. Input over 5,000 characters (raw text or serialised JSON) is now rejected before parsing. | `test_oversized_event_text_is_rejected_before_parsing`, `test_worst_case_event_text_at_the_size_limit_parses_quickly` |
| Deeply nested event input | It raised `RecursionError`; it now raises `ValueError` with a clear message. | `test_deeply_nested_event_input_raises_value_error_not_recursion_error` |
| Scientific-notation strings | `"1e999"` was silently read as `1` (+1 Power). The whole token is now parsed, so it is out of range and ignored with a note, while `"1e3"` reads as 1000. | `test_scientific_notation_strings_are_read_whole` |
| Secrets in tracebacks | Log redaction covered messages but not `exc_info` tracebacks or stack info. Both are now redacted. | `test_log_handler_redacts_secrets_in_tracebacks` |

Residual risks:
- LLM text is rendered as Markdown (links and images would display).
- The app has no authentication; it is a single-user local tool.
- Transitive dependencies are not locked.
