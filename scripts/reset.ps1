# ARES Accord - delete all persisted negotiation history (data/ares.db). Irreversible.
Set-Location (Split-Path $PSScriptRoot -Parent)
Remove-Item -ErrorAction SilentlyContinue data\ares.db, data\ares.db-journal
Write-Host "History wiped. Restart the dashboard."
