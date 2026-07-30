"""Rolling snapshot of active clients.

Run hourly. Each run records every client whose ``last_seen`` falls on the
target UTC day (the dashboard buckets by UTC date), *adding* to what earlier
runs already recorded — so a day's active set is frozen as it happens, before
the next day's activity overwrites ``last_seen``. This is what makes the
"Active clients per day" chart correct for past days (today stays live in the
dashboard).

With no ``--date`` it processes **yesterday and today**. Re-doing yesterday is
what closes the tail of the day: a single end-of-day run at 23:55 could never
see clients active in the last few minutes before midnight, whereas the 00:05
run still finds them (their ``last_seen`` is unchanged until they return). The
one case still lost is a client seen only in that tail *and* again before the
next run — ``last_seen`` has already moved on by the time we look.

State lives in a dashboard-owned SQLite DB (config.DAILY_ACTIVE_DB_PATH):
    daily_active(date, client_id, is_cloud)   -- one row per active client per day
    snapshot_runs(date, computed_at, client_count)

Rows accumulate, so a run can only ever add clients to a day; ``--replace``
rebuilds a date from scratch instead, discarding what was accumulated (only
useful when a day's rows are known to be wrong).

Usage:
    uv run python scripts/snapshot_active.py                        # yesterday + today
    uv run python scripts/snapshot_active.py --date 2026-06-10
    uv run python scripts/snapshot_active.py --date 2026-06-10 --replace
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


def snapshot(target_date: str | None = None, replace: bool = False) -> int:
    """Record active clients for ``target_date`` (UTC ``YYYY-MM-DD``); default today.

    Adds to the day's existing rows unless ``replace`` is True. Returns the
    day's total client count after the run.
    """
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
            if replace:
                store.execute("DELETE FROM daily_active WHERE date = ?", (target_date,))
            before = _day_count(store, target_date)
            # Keep the first run's row, except when a later run resolves an IP as
            # cloud that an earlier one couldn't (reverse DNS can time out).
            store.executemany(
                "INSERT INTO daily_active (date, client_id, is_cloud) VALUES (?, ?, ?) "
                "ON CONFLICT(date, client_id) DO UPDATE SET is_cloud=1 "
                "WHERE excluded.is_cloud = 1",
                records,
            )
            total = _day_count(store, target_date)
            cloud = _day_count(store, target_date, cloud_only=True)
            store.execute(
                "INSERT INTO snapshot_runs (date, computed_at, client_count) VALUES (?, ?, ?) "
                "ON CONFLICT(date) DO UPDATE SET computed_at=excluded.computed_at, "
                "client_count=excluded.client_count",
                (target_date, now, total),
            )
    finally:
        store.close()

    logger.info("Snapshot %s: %d active clients (+%d this run, %d cloud)",
                target_date, total, total - before, cloud)
    return total


def _day_count(store: sqlite3.Connection, date: str, cloud_only: bool = False) -> int:
    cloud = " AND is_cloud = 1" if cloud_only else ""
    return store.execute(
        f"SELECT COUNT(*) FROM daily_active WHERE date = ?{cloud}", (date,)
    ).fetchone()[0]


def main():
    ap = argparse.ArgumentParser(description="Snapshot active clients for a UTC day")
    ap.add_argument("--date", help="UTC date YYYY-MM-DD (default: yesterday and today)")
    ap.add_argument("--replace", action="store_true",
                    help="rebuild the date from scratch instead of adding to it")
    args = ap.parse_args()
    if args.date:
        snapshot(args.date, replace=args.replace)
        return
    # Yesterday first: it can still gain the clients seen after the last run of
    # that day, which is the whole point of re-processing it (see module docs).
    with db.upstream() as src:
        today, yesterday = src.execute("SELECT date('now'), date('now','-1 day')").fetchone()
    for day in (yesterday, today):
        snapshot(day)


if __name__ == "__main__":
    main()
