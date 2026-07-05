"""End-of-day snapshot of active clients.

Run just before UTC midnight (the dashboard buckets by UTC date). It records,
for the target day, every client whose ``last_seen`` falls on that day —
freezing the count before the next day's activity overwrites ``last_seen``.
This is what makes the "Active clients per day" chart correct for past days
(today stays live in the dashboard).

State lives in a dashboard-owned SQLite DB (config.DAILY_ACTIVE_DB_PATH):
    daily_active(date, client_id, is_cloud)   -- one row per active client per day
    snapshot_runs(date, computed_at, client_count)

Idempotent: re-running for a date replaces that date's rows.

Usage:
    uv run python scripts/snapshot_active.py            # snapshot today (UTC)
    uv run python scripts/snapshot_active.py --date 2026-06-10
"""

import argparse
import logging
import os
import sqlite3
import sys
from datetime import datetime, timezone

# Make `src` importable whether run as a file or a module.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import cloud_ips, config, db  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("snapshot_active")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_active (
    date      TEXT NOT NULL,
    client_id TEXT NOT NULL,
    is_cloud  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (date, client_id)
);
CREATE TABLE IF NOT EXISTS snapshot_runs (
    date         TEXT PRIMARY KEY,
    computed_at  TEXT NOT NULL,
    client_count INTEGER NOT NULL
);
"""


def _ensure_store() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DAILY_ACTIVE_DB_PATH), exist_ok=True)
    conn = sqlite3.connect(config.DAILY_ACTIVE_DB_PATH)
    conn.executescript(_SCHEMA)
    return conn


def snapshot(target_date: str | None = None) -> int:
    """Snapshot active clients for ``target_date`` (UTC ``YYYY-MM-DD``); default today."""
    with db.upstream() as src:
        if target_date is None:
            target_date = src.execute("SELECT date('now')").fetchone()[0]
        rows = src.execute(
            "SELECT uuid, first_ip, last_ip FROM clients WHERE date(last_seen) = ?",
            (target_date,),
        ).fetchall()

    # Classify cloud IPs (reverse DNS); prewarm so it's fast.
    cloud_ips.prewarm([ip for r in rows for ip in (r["first_ip"], r["last_ip"]) if ip])
    records = [
        (target_date, r["uuid"],
         1 if (cloud_ips.is_cloud_ip(r["last_ip"]) or cloud_ips.is_cloud_ip(r["first_ip"])) else 0)
        for r in rows
    ]

    now = datetime.now(timezone.utc).isoformat()
    store = _ensure_store()
    try:
        with store:  # transaction
            store.execute("DELETE FROM daily_active WHERE date = ?", (target_date,))
            store.executemany(
                "INSERT INTO daily_active (date, client_id, is_cloud) VALUES (?, ?, ?)", records
            )
            store.execute(
                "INSERT INTO snapshot_runs (date, computed_at, client_count) VALUES (?, ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET computed_at=excluded.computed_at, "
                "client_count=excluded.client_count",
                (target_date, now, len(records)),
            )
    finally:
        store.close()

    logger.info("Snapshot %s: %d active clients (%d cloud)",
                target_date, len(records), sum(r[2] for r in records))
    return len(records)


def main():
    ap = argparse.ArgumentParser(description="Snapshot end-of-day active clients")
    ap.add_argument("--date", help="UTC date YYYY-MM-DD to snapshot (default: today)")
    args = ap.parse_args()
    snapshot(args.date)


if __name__ == "__main__":
    main()
