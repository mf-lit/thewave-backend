"""Schema migrations, tracked by ``PRAGMA user_version``.

Two live consumers read this database directly — the dashboard ATTACHes it
read-only and joins ``notifications``, and sqlite-web browses both tables — so
migrations here are additive only. Nothing renames, drops, or rewrites a
column that already holds production data.

The production database arrives at version 0 with the full schema already in
place (built up by ``CREATE TABLE IF NOT EXISTS`` and an ad-hoc ``ALTER TABLE``
on the old hot path, plus a ``clients.alias`` column added out of band).
Migration 1 therefore reconciles rather than creates: every statement is
conditional, so it is a near no-op against production and a full bootstrap
against an empty file.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable, List, Tuple

from .db import Database

logger = logging.getLogger(__name__)

NOTIFICATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS notifications (
    client_id TEXT NOT NULL,
    notification_id TEXT NOT NULL,
    performance_ak TEXT NOT NULL,
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    side TEXT NOT NULL,
    title TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    thresholds TEXT,
    notified_thresholds TEXT,
    last_checked_availability INTEGER,
    created_at TEXT NOT NULL,
    next_check_at TEXT,
    PRIMARY KEY (client_id, notification_id)
)
"""

CLIENTS_TABLE = """
CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY,
    fcm_token TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    alias TEXT
)
"""


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in _columns(conn, table):
        logger.info("Adding column %s.%s", table, column)
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migration_001_baseline(conn: sqlite3.Connection) -> None:
    conn.execute(NOTIFICATIONS_TABLE)
    conn.execute(CLIENTS_TABLE)

    # Present in production but not in the original CREATE statements.
    _add_column_if_missing(conn, "notifications", "next_check_at", "TEXT")
    _add_column_if_missing(conn, "clients", "alias", "TEXT")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_performance_ak ON notifications(performance_ak)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON notifications(date)")
    # The worker's only query filters on this column every cycle.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_next_check_at ON notifications(next_check_at)")


def _migration_002_quiet_session(conn: sqlite3.Connection) -> None:
    """The two columns quiet_session needs, both nullable and unused elsewhere.

    ``minimum_slots`` is deliberately not folded into ``thresholds``: that
    column means "alert at or below", and this one means "alert at or above",
    so sharing it would leave the direction implied by ``notification_type``
    and invisible to anything reading the table.

    ``time_before`` holds the canonical duration string ("24h") rather than an
    hour count, so other units can be added without reinterpreting old rows.
    """
    _add_column_if_missing(conn, "notifications", "minimum_slots", "INTEGER")
    _add_column_if_missing(conn, "notifications", "time_before", "TEXT")


def _migration_003_any_quiet_session(conn: sqlite3.Connection) -> None:
    """The repeat guard for any_quiet_session.

    A JSON *object* of performance_ak -> session date, not the JSON array
    ``notified_thresholds`` uses. That row watches one session and can name its
    thresholds up front; this one watches a rolling window and cannot know
    which sessions it will fire for, so it accumulates them. The stored date is
    what lets an entry be dropped once its session is over, keeping the map
    bounded on a row that outlives every session it notifies about — pruning
    against what happens to be visible in the calendar instead would re-notify
    the moment upstream returned a short response.
    """
    _add_column_if_missing(conn, "notifications", "notified_performances", "TEXT")


def _migration_004_session_filters(conn: sqlite3.Connection) -> None:
    """The day and time-of-day filters for any_quiet_session.

    All three are nullable and NULL means "no filter on this axis", which is
    what every row written before this migration reads as — the type's existing
    behaviour is the behaviour of a row with none of them set.

    ``days`` is a JSON *array* of canonical short names ('["sat","sun"]') rather
    than a bitmask or a set of seven boolean columns, for the same reason
    ``time_before`` holds "24h" and not an hour count: the column has to mean
    something on its own to whoever is reading the table in sqlite-web.

    ``not_before``/``not_after`` are canonical HH:MM in Europe/London wall-clock,
    the same convention as the ``time`` column, so they are directly comparable
    with a session's start time without either being parsed.
    """
    _add_column_if_missing(conn, "notifications", "days", "TEXT")
    _add_column_if_missing(conn, "notifications", "not_before", "TEXT")
    _add_column_if_missing(conn, "notifications", "not_after", "TEXT")


MIGRATIONS: List[Tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001_baseline),
    (2, _migration_002_quiet_session),
    (3, _migration_003_any_quiet_session),
    (4, _migration_004_session_filters),
]

LATEST_VERSION = MIGRATIONS[-1][0]


def apply(database: Database) -> int:
    """Bring the database up to ``LATEST_VERSION``. Returns the new version.

    ``BEGIN IMMEDIATE`` inside ``transaction()`` serialises this against the
    other process, so the API and worker can start simultaneously.
    """
    with database.transaction() as conn:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        for version, migrate in MIGRATIONS:
            if version <= current:
                continue
            logger.info("Applying migration %d", version)
            migrate(conn)
            # PRAGMA does not accept parameters; version is a module constant.
            conn.execute(f"PRAGMA user_version = {version}")
            current = version
    return current
