#!/usr/bin/env bash
# Hourly water-temperature forecast run.
# Refreshes the anchor from the source CSV, generates a 168h forecast, and
# writes forecast/<Y>/<M>/<D>/<H>.csv plus forecast/latest.csv.
#
# Any arguments are passed straight through to `forecast-lake`, e.g.:
#   run_forecast.sh --latest-path /srv/wave/forecast.csv
#
# Schedule hourly via crontab, e.g.:
#   0 * * * * /server-setup/thewave/ml/scripts/run_forecast.sh >> /server-setup/thewave/ml/logs/forecast.log 2>&1
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
mkdir -p logs

echo "=== forecast run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
/home/marc/.local/bin/uv run forecast-lake "$@"
