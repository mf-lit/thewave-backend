"""Bootstrap and re-apply.

The additive-only rule means these two are the whole contract for now: a fresh
file arrives at the latest version, and applying again changes nothing.
"""
from __future__ import annotations

from pathlib import Path

from src import migrations
from src.db import Database


def columns(database: Database, table: str) -> set:
    conn = database.connection()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def test_bootstraps_a_fresh_file(tmp_path: Path):
    database = Database(tmp_path / "messages.db")
    assert migrations.apply(database) == migrations.LATEST_VERSION

    assert "priority" in columns(database, "messages")
    assert columns(database, "message_acks") == {
        "message_id",
        "client_id",
        "revision",
        "acked_at",
    }


def test_re_apply_is_a_no_op(tmp_path: Path):
    database = Database(tmp_path / "messages.db")
    migrations.apply(database)
    before = columns(database, "messages")

    assert migrations.apply(database) == migrations.LATEST_VERSION
    assert columns(database, "messages") == before


def test_acks_are_indexed_by_client_id(tmp_path: Path):
    """Every /messages request loads one client's acks; the PK cannot serve that."""
    database = Database(tmp_path / "messages.db")
    migrations.apply(database)

    plan = database.connection().execute(
        "EXPLAIN QUERY PLAN SELECT message_id, revision FROM message_acks "
        "WHERE client_id = ?",
        ("some-client",),
    ).fetchall()
    assert any("idx_message_acks_client_id" in row["detail"] for row in plan)
