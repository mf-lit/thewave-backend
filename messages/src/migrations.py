"""Schema migrations, tracked by ``PRAGMA user_version``.

Same list-of-callables shape as ``notifications/src/migrations.py``, and the
same rule: **additive only**. sqlite-web will browse this file and the
dashboard may come to read it directly, so nothing here renames, drops, or
rewrites a column that already holds data.

Unlike notifications, this database starts empty — there is no production file
to reconcile with — so migration 1 is a plain bootstrap rather than a set of
conditional statements.
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Callable, List, Tuple

from .db import Database

logger = logging.getLogger(__name__)

MESSAGES_TABLE = """
CREATE TABLE IF NOT EXISTS messages (
    message_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    client_ids TEXT,
    os TEXT,
    min_version TEXT,
    max_version TEXT,
    min_days_count INTEGER,
    max_days_count INTEGER,
    starts_at TEXT NOT NULL,
    ends_at TEXT,
    retain INTEGER NOT NULL DEFAULT 0,
    expires_at TEXT,
    display TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'info',
    priority INTEGER NOT NULL DEFAULT 0,
    action_url TEXT,
    action_label TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

# `revision` is the revision the client acked, not the current one: bumping
# messages.revision past it is what re-serves an edited message to someone who
# has already seen the old wording.
ACKS_TABLE = """
CREATE TABLE IF NOT EXISTS message_acks (
    message_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    acked_at TEXT NOT NULL,
    PRIMARY KEY (message_id, client_id)
)
"""


def _migration_001_baseline(conn: sqlite3.Connection) -> None:
    conn.execute(MESSAGES_TABLE)
    conn.execute(ACKS_TABLE)
    # Every /messages request loads one client's acks by client_id; the primary
    # key leads with message_id and cannot serve that.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_message_acks_client_id "
        "ON message_acks(client_id)"
    )


def _columns(conn: sqlite3.Connection, table: str) -> set:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in _columns(conn, table):
        logger.info("Adding column %s.%s", table, column)
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _migration_002_dismissal_delay(conn: sqlite3.Connection) -> None:
    """How long a banner must stay on screen before it can be dismissed.

    Both nullable, and NULL on both means "dismissable at once" — which is what
    every row written before this migration reads as, and what the client did
    for all of them.

    ``dismissable_after`` holds a canonical duration string ("30s", "5m") rather
    than a count of seconds, following the precedent set by
    ``notifications.time_before``: the column has to mean something on its own
    to whoever is reading the table in sqlite-web, and storing the unit means
    another one can be added later without reinterpreting old rows.

    ``dismissable_at`` is an absolute UTC stamp in the same form as every other
    timestamp here, so the two are directly comparable without either being
    parsed.
    """
    _add_column_if_missing(conn, "messages", "dismissable_at", "TEXT")
    _add_column_if_missing(conn, "messages", "dismissable_after", "TEXT")


def _migration_003_banner_title(conn: sqlite3.Connection) -> None:
    """The line a banner shows, which is required of every new banner.

    The column stays nullable — a modal and an inbox entry have nowhere to draw
    one — but ``models`` will not accept a banner without it, so the backfill
    below is what makes that true of the rows already here rather than only of
    the ones written next.

    Copying ``title`` is exactly what those banners already displayed, so this
    changes no behaviour; it moves a fallback out of the client and into the
    data. It is the one data write in this file, and it does not break the
    additive-only rule: the column it writes was created three lines above and
    holds nothing.
    """
    _add_column_if_missing(conn, "messages", "banner_title", "TEXT")
    conn.execute(
        "UPDATE messages SET banner_title = title "
        "WHERE display = 'banner' AND (banner_title IS NULL OR banner_title = '')"
    )


MIGRATIONS: List[Tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001_baseline),
    (2, _migration_002_dismissal_delay),
    (3, _migration_003_banner_title),
]

LATEST_VERSION = MIGRATIONS[-1][0]


def apply(database: Database) -> int:
    """Bring the database up to ``LATEST_VERSION``. Returns the new version.

    ``BEGIN IMMEDIATE`` inside ``transaction()`` serialises this against the
    other gunicorn worker, so both can start simultaneously.
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
