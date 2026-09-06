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


MIGRATIONS: List[Tuple[int, Callable[[sqlite3.Connection], None]]] = [
    (1, _migration_001_baseline),
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
