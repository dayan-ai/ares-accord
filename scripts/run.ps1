# ARES Accord - create venv (first run), install deps, launch dashboard
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path ".venv")) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -q -r requirements.txt
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created .env from .env.example (rule-based mode until you add a key)." }
.\.venv\Scripts\python -m streamlit run app.py
