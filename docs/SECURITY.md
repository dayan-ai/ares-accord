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
