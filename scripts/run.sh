#!/usr/bin/env bash
# ARES Accord - create venv (first run), install deps, launch dashboard
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
PY=.venv/bin/python; [ -x "$PY" ] || PY=.venv/Scripts/python
"$PY" -m pip install -q -r requirements.txt
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example (rule-based mode until you add a key)."; }
"$PY" -m streamlit run app.py
