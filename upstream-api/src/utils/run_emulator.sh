#!/bin/bash
# Quick start script for the upstream API emulator

# Default values
TIMESHIFT_DAYS=${TIMESHIFT_DAYS:-10}
PORT=${PORT:-5001}

echo "Starting Upstream API Emulator"
echo "=============================="
echo "Timeshift: ${TIMESHIFT_DAYS} days"
echo "Port: ${PORT}"
echo ""
echo "To change settings, use environment variables:"
echo "  TIMESHIFT_DAYS=5 PORT=5002 ./run_emulator.sh"
echo ""
echo "Press Ctrl+C to stop"
echo "=============================="
echo ""

python emulate_upstream.py --timeshift-days ${TIMESHIFT_DAYS} --port ${PORT}
