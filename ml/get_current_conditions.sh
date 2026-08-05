#!/bin/bash
set -euo pipefail
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
pushd "$SCRIPT_DIR"

UV=$(/usr/bin/which uv 2>/dev/null || true)
[[ -z "$UV" ]] && UV="/home/marc/.local/bin/uv"

export VIRTUAL_ENV="$SCRIPT_DIR/.venv"
$UV run python get_current_conditions.py >> ${SCRIPT_DIR}/data/hourly.csv

popd > /dev/null 2>&1
