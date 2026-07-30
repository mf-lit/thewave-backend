"""Storage behaviour, including the concurrency the old layer could not do."""
from __future__ import annotations

import json
import sqlite3
import threading

from src.models import NotificationRequest
from src.repository import ClientRepository, NotificationRepository
from tests.conftest import VALID_TOKEN


def request(**overrides) -> NotificationRequest:
    payload = {
        "performance_ak": "TWB.EVN6.PRF8962",
        "date": "2027-08-05",
        "time": "18:00",
        "side": "right",
        "notification_type": "above_zero",
    }
    payload.update(overrides)
    return NotificationRequest.from_payload(payload)


def raw(services, notification):
    return services.database.connection().execute(
        "SELECT * FROM notifications WHERE notification_id = ?",
        (notification.notification_id,),
    ).fetchone()


# -- storage format -----------------------------------------------------------

def test_thresholds_are_stored_as_json_text(services):
    """The dashboard parses these columns as JSON."""
    notification = services.notifications.create(
        "c1", request(notification_type="below_threshold", thresholds=[5, 2]), "Advanced Surf"
    )
    row = raw(services, notification)
    assert json.loads(row["thresholds"]) == [5, 2]
    assert json.loads(row["notified_thresholds"]) == []


def test_above_zero_leaves_the_threshold_columns_null(services):
    notification = services.notifications.create("c1", request(), "Advanced Surf")
    row = raw(services, notification)
    assert row["thresholds"] is None and row["notified_thresholds"] is None


def test_a_new_notification_has_no_reading_and_no_schedule(services):
    row = raw(services, services.notifications.create("c1", request(), "Advanced Surf"))
    assert row["last_checked_availability"] is None
    assert row["next_check_at"] is None


def test_created_at_is_written_in_the_established_format(services):
    notification = services.notifications.create("c1", request(), "Advanced Surf")
    assert "T" in notification.created_at and "+" not in notification.created_at


# -- round trips --------------------------------------------------------------

def test_round_trip_preserves_thresholds(services):
    created = services.notifications.create(
        "c1", request(notification_type="below_threshold", thresholds=[5, 2]), "T"
    )
    fetched = services.notifications.get("c1", created.notification_id)
    assert fetched.thresholds == [5, 2]
    assert fetched.notified_thresholds == []


def test_get_is_scoped_by_client(services):
    created = services.notifications.create("c1", request(), "T")
    assert services.notifications.get("c2", created.notification_id) is None


def test_delete_reports_whether_anything_went(services):
    created = services.notifications.create("c1", request(), "T")
    assert services.notifications.delete("c1", created.notification_id) is True
    assert services.notifications.delete("c1", created.notification_id) is False


def test_delete_for_client_returns_a_count(services):
    for _ in range(3):
        services.notifications.create("c1", request(), "T")
    services.notifications.create("c2", request(), "T")

    assert services.notifications.delete_for_client("c1") == 3
    assert len(services.notifications.list_for_client("c2")) == 1


# -- the worker's queries -----------------------------------------------------

def test_unscheduled_notifications_are_due(services):
    services.notifications.create("c1", request(), "T")
    assert len(services.notifications.due()) == 1


def test_a_future_check_is_not_due(services):
    notification = services.notifications.create("c1", request(), "T")
    services.notifications.reschedule(notification, "2099-01-01 00:00:00")
    assert services.notifications.due() == []


def test_a_past_check_is_due_again(services):
    notification = services.notifications.create("c1", request(), "T")
    services.notifications.reschedule(notification, "2000-01-01 00:00:00")
    assert len(services.notifications.due()) == 1


def test_next_due_at_is_none_while_anything_is_unscheduled(services):
    services.notifications.create("c1", request(), "T")
    assert services.notifications.next_due_at() is None


def test_next_due_at_reports_the_earliest(services):
    first = services.notifications.create("c1", request(), "T")
    second = services.notifications.create("c2", request(), "T")
    services.notifications.reschedule(first, "2030-01-01 00:00:00")
    services.notifications.reschedule(second, "2029-01-01 00:00:00")

    assert services.notifications.next_due_at() == "2029-01-01 00:00:00"


def test_clear_notified_thresholds_only_touches_threshold_notifications(services):
    threshold = services.notifications.create(
        "c1", request(notification_type="below_threshold", thresholds=[5]), "T"
    )
    above = services.notifications.create("c2", request(), "T")
    services.notifications.record_notified_thresholds(threshold, [5])

    assert services.notifications.clear_notified_thresholds() == 1
    assert services.notifications.get("c1", threshold.notification_id).notified_thresholds == []
    assert raw(services, above)["notified_thresholds"] is None


# -- client tokens ------------------------------------------------------------

def test_token_upsert_replaces_and_restamps(services):
    first = services.clients.upsert_token("c1", VALID_TOKEN)
    second = services.clients.upsert_token("c1", "e" * 161)

    assert services.clients.get_token("c1") == "e" * 161
    assert second >= first
    assert services.clients.count() == 1


def test_existing_ids_filters_to_clients_with_tokens(services):
    services.clients.upsert_token("c1", VALID_TOKEN)
    assert services.clients.existing_ids(["c1", "c2"]) == {"c1"}
    assert services.clients.existing_ids([]) == set()


def test_blank_tokens_are_findable(services):
    services.clients.upsert_token("c1", VALID_TOKEN)
    with services.database.transaction() as conn:
        conn.execute("INSERT INTO clients VALUES ('c2', '  ', '2026-01-01', NULL)")
    assert services.clients.ids_with_blank_tokens() == ["c2"]


# -- concurrency --------------------------------------------------------------

def test_each_thread_gets_its_own_connection(services):
    seen = []

    def record():
        seen.append(id(services.database.connection()))

    threads = [threading.Thread(target=record) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(seen)) == 4


def test_writes_from_several_threads_all_land(services):
    """The old layer shared one connection across every gunicorn thread."""
    errors = []

    def create(index):
        try:
            NotificationRepository(services.database).create(f"c{index}", request(), "T")
        except Exception as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(services.notifications.all()) == 8


def test_a_reader_is_not_blocked_by_an_open_write(services):
    """WAL: the dashboard reads this file while the worker writes to it."""
    services.notifications.create("c1", request(), "T")

    with services.database.transaction() as conn:
        conn.execute("UPDATE notifications SET title = 'mid-write'")

        reader = sqlite3.connect(f"file:{services.settings.db_path}?mode=ro", uri=True)
        reader.row_factory = sqlite3.Row
        rows = reader.execute("SELECT title FROM notifications").fetchall()
        reader.close()

    assert [row["title"] for row in rows] == ["T"]  # sees the pre-write snapshot


def test_a_failed_write_rolls_back(services):
    created = services.notifications.create("c1", request(), "T")
    try:
        with services.database.transaction() as conn:
            conn.execute("UPDATE notifications SET title = 'changed'")
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert services.notifications.get("c1", created.notification_id).title == "T"


def test_repositories_can_be_built_independently(services):
    """Nothing holds a module-level connection any more."""
    notifications = NotificationRepository(services.database)
    clients = ClientRepository(services.database)
    created = notifications.create("c1", request(), "T")
    clients.upsert_token("c1", VALID_TOKEN)

    assert services.notifications.get("c1", created.notification_id) is not None
    assert services.clients.get_token("c1") == VALID_TOKEN
