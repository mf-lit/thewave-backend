"""Migrations must never cost a live notification.

The most important test in the suite runs against a byte-copy of the
production database taken before the rewrite: 11 notifications and 436 client
tokens that real people are waiting on.
"""
from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest

from src import migrations
from src.db import Database

PRODUCTION_SNAPSHOT = Path("/thewave/db_backup/notifications-pre-rewrite.db")

# The columns the dashboard selects when it ATTACHes this database read-only.
DASHBOARD_COLUMNS = {
    "client_id",
    "notification_id",
    "performance_ak",
    "title",
    "date",
    "time",
    "side",
    "notification_type",
    "thresholds",
    "notified_thresholds",
    "last_checked_availability",
    "created_at",
}


def dump(path: Path) -> list:
    with sqlite3.connect(path) as conn:
        return list(conn.iterdump())


def columns(database: Database, table: str) -> set:
    return {row["name"] for row in database.connection().execute(f"PRAGMA table_info({table})")}


# -- a fresh install ----------------------------------------------------------

def test_an_empty_file_is_bootstrapped(tmp_path):
    database = Database(tmp_path / "new.db")
    assert migrations.apply(database) == migrations.LATEST_VERSION
    assert DASHBOARD_COLUMNS <= columns(database, "notifications")
    assert {"client_id", "fcm_token", "updated_at", "alias"} <= columns(database, "clients")


def test_migrations_are_idempotent(tmp_path):
    database = Database(tmp_path / "new.db")
    migrations.apply(database)
    before = dump(tmp_path / "new.db")

    assert migrations.apply(database) == migrations.LATEST_VERSION
    assert dump(tmp_path / "new.db") == before


def test_the_journal_is_wal(tmp_path):
    """Two processes share this file; the old rollback journal blocked readers."""
    database = Database(tmp_path / "new.db")
    migrations.apply(database)
    mode = database.connection().execute("PRAGMA journal_mode").fetchone()[0]
    assert mode == "wal"


# -- the schema the previous implementation actually produced -----------------

def legacy_database(path: Path) -> Database:
    """Recreate the pre-rewrite schema, including its ad-hoc additions."""
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE notifications (
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
                created_at TEXT NOT NULL, next_check_at TEXT,
                PRIMARY KEY (client_id, notification_id)
            );
            CREATE INDEX idx_performance_ak ON notifications(performance_ak);
            CREATE INDEX idx_date ON notifications(date);
            CREATE TABLE clients (
                client_id TEXT PRIMARY KEY,
                fcm_token TEXT NOT NULL,
                updated_at TEXT NOT NULL
            , alias TEXT);
            INSERT INTO notifications VALUES
                ('c1','n1','TWB.EVN6.PRF8966','2026-09-02','18:00','right','Advanced Surf',
                 'below_threshold','[5, 4, 3]','[]',11,'2026-07-30 19:50:11','2026-07-05T20:06:33.237402');
            INSERT INTO clients VALUES ('c1','token-1','2026-07-05T20:00:00','marc');
            """
        )
    return Database(path)


def test_legacy_schema_migrates_without_touching_rows(tmp_path):
    path = tmp_path / "legacy.db"
    database = legacy_database(path)

    before = database.connection().execute("SELECT * FROM notifications").fetchall()
    assert migrations.apply(database) == migrations.LATEST_VERSION
    after = database.connection().execute("SELECT * FROM notifications").fetchall()

    assert [dict(row) for row in after] == [dict(row) for row in before]


def test_an_out_of_band_column_survives(tmp_path):
    """`clients.alias` was added by hand in production and is not ours to drop."""
    database = legacy_database(tmp_path / "legacy.db")
    migrations.apply(database)

    assert "alias" in columns(database, "clients")
    assert database.connection().execute(
        "SELECT alias FROM clients WHERE client_id = 'c1'"
    ).fetchone()["alias"] == "marc"


def test_a_database_predating_next_check_at_gains_the_column(tmp_path):
    path = tmp_path / "older.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE notifications (
                client_id TEXT NOT NULL, notification_id TEXT NOT NULL,
                performance_ak TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL,
                side TEXT NOT NULL, title TEXT NOT NULL, notification_type TEXT NOT NULL,
                thresholds TEXT, notified_thresholds TEXT,
                last_checked_availability INTEGER, created_at TEXT NOT NULL,
                PRIMARY KEY (client_id, notification_id)
            );
            INSERT INTO notifications VALUES
                ('c1','n1','ak','2026-09-02','18:00','right','t','above_zero',NULL,NULL,3,'2026-07-05T20:06:33');
            """
        )
    database = Database(path)
    migrations.apply(database)

    assert "next_check_at" in columns(database, "notifications")
    row = database.connection().execute("SELECT * FROM notifications").fetchone()
    assert row["next_check_at"] is None and row["last_checked_availability"] == 3


# -- the real thing -----------------------------------------------------------

@pytest.mark.skipif(
    not PRODUCTION_SNAPSHOT.exists(), reason="production snapshot not available"
)
def test_production_snapshot_survives_migration(tmp_path):
    path = tmp_path / "production.db"
    shutil.copy(PRODUCTION_SNAPSHOT, path)

    original = sqlite3.connect(path)
    original.row_factory = sqlite3.Row
    before_notifications = [
        dict(r) for r in original.execute("SELECT * FROM notifications ORDER BY notification_id")
    ]
    before_clients = [dict(r) for r in original.execute("SELECT * FROM clients ORDER BY client_id")]
    original.close()
    assert (len(before_notifications), len(before_clients)) > (0, 0)

    database = Database(path)
    assert migrations.apply(database) == migrations.LATEST_VERSION

    conn = database.connection()
    after_notifications = [
        dict(r) for r in conn.execute("SELECT * FROM notifications ORDER BY notification_id")
    ]
    after_clients = [dict(r) for r in conn.execute("SELECT * FROM clients ORDER BY client_id")]

    assert len(after_notifications) == len(before_notifications)
    assert len(after_clients) == len(before_clients)
    assert after_notifications == before_notifications
    assert after_clients == before_clients
    assert DASHBOARD_COLUMNS <= columns(database, "notifications")
