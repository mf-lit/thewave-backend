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


def test_migration_002_adds_the_dismissal_columns(tmp_path: Path):
    database = Database(tmp_path / "messages.db")
    migrations.apply(database)
    assert {"dismissable_at", "dismissable_after"} <= columns(database, "messages")


def test_migration_003_adds_the_banner_title_column(tmp_path: Path):
    database = Database(tmp_path / "messages.db")
    migrations.apply(database)
    assert "banner_title" in columns(database, "messages")


def at_version_2(tmp_path: Path) -> Database:
    """A database as the deployed file was before the banner-title column."""
    database = Database(tmp_path / "messages.db")
    with database.transaction() as conn:
        migrations._migration_001_baseline(conn)
        migrations._migration_002_dismissal_delay(conn)
        conn.execute("PRAGMA user_version = 2")
    return database


def test_migration_003_is_additive_over_a_version_2_database(tmp_path: Path):
    database = at_version_2(tmp_path)
    before = columns(database, "messages")
    assert "banner_title" not in before

    assert migrations.apply(database) == migrations.LATEST_VERSION
    after = columns(database, "messages")
    assert before < after
    assert after - before == {"banner_title"}


def test_migration_003_backfills_existing_banners_from_their_title(tmp_path: Path):
    """What those banners already displayed, moved out of the client's fallback.

    A banner cannot be *written* without a title of its own, so the rows that
    predate the column are backfilled to make that true of them too. Copying
    `title` changes no behaviour: it is what the banner drew anyway.
    """
    database = at_version_2(tmp_path)
    with database.transaction() as conn:
        for message_id, display in (("old-banner", "banner"), ("old-modal", "modal")):
            conn.execute(
                "INSERT INTO messages (message_id, title, body, starts_at, display, "
                "level, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id, f"Title of the {display}", "Body.", "2026-01-01T00:00:00+00:00",
                 display, "info", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
            )

    migrations.apply(database)

    stored = {
        row["message_id"]: row["banner_title"]
        for row in database.connection().execute(
            "SELECT message_id, banner_title FROM messages"
        )
    }
    assert stored["old-banner"] == "Title of the banner"
    # Nowhere to draw one, so it stays NULL — the backfill is not a default.
    assert stored["old-modal"] is None


def test_every_later_migration_is_additive_over_a_version_1_database(tmp_path: Path):
    """A baseline file catches up by ALTER, never by a create.

    The oldest shape anything runs against, so this is the one that would catch
    a migration that rewrote the table instead of adding to it.
    """
    database = Database(tmp_path / "messages.db")
    with database.transaction() as conn:
        migrations._migration_001_baseline(conn)
        conn.execute("PRAGMA user_version = 1")
    before = columns(database, "messages")
    assert "dismissable_at" not in before

    assert migrations.apply(database) == migrations.LATEST_VERSION
    after = columns(database, "messages")
    # Nothing dropped or renamed; exactly the columns the later migrations add.
    assert before < after
    assert after - before == {"dismissable_at", "dismissable_after", "banner_title"}
