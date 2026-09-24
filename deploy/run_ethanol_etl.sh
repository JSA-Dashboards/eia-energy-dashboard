#!/usr/bin/env bash
# run_ethanol_etl.sh — pulls fuel-ethanol data from the live EIA API and
# merges it into JSA.EIA_ETHANOL_CACHE.ETHANOL_WEEKLY, invoked by cron on
# the Droplet. The Streamlit app reads that cache instead of calling EIA
# live (see eia_ethanol_cache_client.py) -- no propagation step needed,
# every deployed instance sees the update on its next page load.
#
# Cron installs this; adjust APP_DIR to wherever the repo is deployed.
set -uo pipefail

APP_DIR="/opt/eia-energy-dashboard"          # <-- deploy path (edit me)
VENV="$APP_DIR/.venv"                         # virtualenv created during setup
LOG_DIR="$APP_DIR/logs"

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/ethanol_etl_$(date +%Y%m%d_%H%M%S).log"

cd "$APP_DIR" || { echo "APP_DIR $APP_DIR missing" >&2; exit 1; }

# Single-instance guard: skip silently if a run is already going.
exec 9>"$LOG_DIR/.ethanol_etl.lock"
if ! flock -n 9; then
    echo "$(date -Is) another run is in progress — skipping" >>"$LOG"
    exit 0
fi

rc=0
{
    echo "=== ethanol ETL start $(date -Is) ==="
    git pull --quiet
    "$VENV/bin/python" deploy/run_ethanol_etl.py
    rc=$?
    echo "=== ethanol ETL finished $(date -Is) rc=$rc ==="
} >>"$LOG" 2>&1

# Keep 30 days of logs.
find "$LOG_DIR" -name 'ethanol_etl_*.log' -mtime +30 -delete 2>/dev/null || true

exit "$rc"
