#!/usr/bin/env bash
# ARES Accord - delete all persisted negotiation history (data/ares.db). Irreversible.
cd "$(dirname "$0")/.."
rm -f data/ares.db data/ares.db-journal
echo "History wiped. Restart the dashboard."
