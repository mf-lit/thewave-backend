"""Reading upstream-api's clients table, and every way that can fail.

The degradation cases are the point of this file. That schema is managed by
``client_tracker.py``'s ad-hoc ``try: ALTER TABLE … except: pass``, so its
shape is not something this service can assume — and a messages endpoint that
500s because a column it only uses for targeting is missing would be a worse
outage than the one it is reporting.
"""
from __future__ import annotations

import logging
from pathlib import Path

from src.directory import ClientDirectory
from src.targeting import DEFAULT_DAYS_COUNT

from conftest import build_upstream_db

CLIENT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ID = "22222222-2222-2222-2222-222222222222"

ROWS = [
    {
        "uuid": CLIENT_ID,
        "first_seen": "2026-01-01T00:00:00+00:00",
        "last_seen": "2026-06-01T00:00:00+00:00",
        "request_count": 40,
        "days_count": 12,
        "client_os": "ios",
        "client_version": "1.2.3",
    },
    {
        "uuid": OTHER_ID,
        "first_seen": "2026-05-30T00:00:00+00:00",
        "last_seen": "2026-06-01T00:00:00+00:00",
        "request_count": 2,
        "days_count": 1,
        "client_os": "android",
        "client_version": "1.0.0",
    },
]


def directory(tmp_path: Path, **kwargs) -> ClientDirectory:
    path = build_upstream_db(tmp_path / "water_temperature.db", ROWS, **kwargs)
    return ClientDirectory(path)


def test_lookup_returns_days_count(tmp_path: Path):
    assert directory(tmp_path).lookup(CLIENT_ID) == 12


def test_lookup_of_an_unknown_client_is_none(tmp_path: Path):
    assert directory(tmp_path).lookup("no-such-client") is None


def test_iter_clients_carries_the_targeting_fields(tmp_path: Path):
    clients = {client.client_id: client for client in directory(tmp_path).iter_clients()}

    assert set(clients) == {CLIENT_ID, OTHER_ID}
    assert clients[CLIENT_ID].client_os == "ios"
    assert clients[CLIENT_ID].client_version == "1.2.3"
    assert clients[CLIENT_ID].days_count == 12


def test_a_missing_file_degrades_to_nothing(tmp_path: Path):
    absent = ClientDirectory(tmp_path / "never-created.db")
    assert absent.lookup(CLIENT_ID) is None
    assert absent.iter_clients() == []


def test_a_missing_file_is_retried_rather_than_cached(tmp_path: Path):
    """upstream-api may create the file after this process starts."""
    path = tmp_path / "water_temperature.db"
    later = ClientDirectory(path)
    assert later.lookup(CLIENT_ID) is None

    build_upstream_db(path, ROWS)
    assert later.lookup(CLIENT_ID) == 12


def test_a_missing_table_degrades_to_nothing(tmp_path: Path):
    """PRAGMA table_info on an absent table returns no rows rather than raising."""
    empty = directory(tmp_path, table="something_else")
    assert empty.lookup(CLIENT_ID) is None
    assert empty.iter_clients() == []


def test_a_missing_days_count_column_degrades_to_the_default(tmp_path: Path):
    partial = directory(tmp_path, columns=("uuid", "client_os", "client_version"))

    assert partial.lookup(CLIENT_ID) is None
    clients = {client.client_id: client for client in partial.iter_clients()}
    assert clients[CLIENT_ID].client_os == "ios"
    assert clients[CLIENT_ID].days_count == DEFAULT_DAYS_COUNT


def test_a_missing_os_column_costs_only_that_column(tmp_path: Path):
    """The SELECT is built from what exists, so one gap is not a total loss."""
    partial = directory(tmp_path, columns=("uuid", "days_count"))

    assert partial.lookup(CLIENT_ID) == 12
    clients = {client.client_id: client for client in partial.iter_clients()}
    assert clients[CLIENT_ID].client_os is None
    assert clients[CLIENT_ID].days_count == 12


def test_a_missing_uuid_column_degrades_to_nothing(tmp_path: Path):
    unusable = directory(tmp_path, columns=("days_count", "client_os"))
    assert unusable.lookup(CLIENT_ID) is None
    assert unusable.iter_clients() == []


def test_a_null_days_count_reads_as_unknown(tmp_path: Path):
    path = build_upstream_db(
        tmp_path / "water_temperature.db", [{"uuid": CLIENT_ID, "client_os": "ios"}]
    )
    assert ClientDirectory(path).lookup(CLIENT_ID) is None


def test_it_warns_once_and_not_once_per_request(tmp_path: Path, caplog):
    """A persistent condition on a per-request path; log-alerts reads this output."""
    absent = ClientDirectory(tmp_path / "never-created.db")

    with caplog.at_level(logging.WARNING, logger="src.directory"):
        for _ in range(5):
            absent.lookup(CLIENT_ID)

    assert len(caplog.records) == 1


def test_it_never_opens_a_writable_connection(tmp_path: Path):
    """The invariant: this service must not be able to modify upstream's file."""
    reader = directory(tmp_path)
    reader.lookup(CLIENT_ID)

    conn = reader._connection()
    try:
        conn.execute("UPDATE clients SET days_count = 99")
        raise AssertionError("expected a read-only connection")
    except Exception as exc:
        assert "readonly" in str(exc).lower()
