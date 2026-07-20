#!/usr/bin/env bash
# Monthly retrain-and-promote run.
# Refreshes data, trains a fresh GRU on a rolling split, and promotes it only if
# it strictly beats the deployed model on the same test+audit windows. The prior
# best is snapshotted to models/archive/<ts>/ for rollback.
#
# Any arguments pass straight through to `retrain-lake`, e.g.:
#   run_retrain.sh --test-days 120
#
# Schedule monthly via crontab (03:30 on the 1st of each month), e.g.:
#   30 3 1 * * /server-setup/thewave/ml/scripts/run_retrain.sh >> /server-setup/thewave/ml/logs/retrain.log 2>&1
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"
mkdir -p logs

echo "=== retrain run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
uv run retrain-lake "$@"
