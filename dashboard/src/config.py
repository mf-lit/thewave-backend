"""Configuration for the dashboard.

Database paths default to the real on-disk locations so the app runs with no
environment setup when launched as a user that can read them (e.g. ``marc``).
Override via the ``UPSTREAM_DB_PATH`` / ``NOTIFICATIONS_DB_PATH`` env vars.
"""

import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# water_temperature.db — holds the `clients` table (uuid, first_seen, last_seen, alias, ...)
UPSTREAM_DB_PATH = os.environ.get(
    "UPSTREAM_DB_PATH",
    "/docker_vols/thewave/upstream-api/data/water_temperature.db",
)

# notifications.db — holds the `notifications` table
NOTIFICATIONS_DB_PATH = os.environ.get(
    "NOTIFICATIONS_DB_PATH",
    "/docker_vols/thewave/notifications/data/notifications.db",
)

# daily_active.db — the dashboard's own WRITABLE store of end-of-day active-client
# snapshots (written by scripts/snapshot_active.py, read by the active chart).
DAILY_ACTIVE_DB_PATH = os.environ.get(
    "DAILY_ACTIVE_DB_PATH",
    os.path.join(_PROJECT_ROOT, "data", "daily_active.db"),
)
