#!/bin/bash
set -e

# Function to handle shutdown signals
cleanup() {
    echo "Received shutdown signal, stopping services..."
    kill -TERM "$api_pid" "$daemon_pid" 2>/dev/null || true
    wait "$api_pid" "$daemon_pid" 2>/dev/null || true
    exit 0
}

# Set up signal handlers
trap cleanup SIGTERM SIGINT

# Start the API server in the background
echo "Starting API server..."
# Use gunicorn for production (via UV run to use the correct environment)
uv run gunicorn -w 2 -b 0.0.0.0:5001 --access-logfile - --error-logfile - "src.api.app:create_app()" &
api_pid=$!

# Start the daemon scheduler in the background
echo "Starting daemon scheduler..."
uv run python -m src.daemon.scheduler &
daemon_pid=$!

# Wait for both processes
wait $api_pid $daemon_pid
