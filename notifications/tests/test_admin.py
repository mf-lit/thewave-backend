"""The maintenance CLI."""
from __future__ import annotations

import pytest

from src import admin
from src.models import NotificationRequest
from tests.conftest import VALID_TOKEN


def add(services, client_id, **overrides):
    payload = {
        "performance_ak": "TWB.EVN6.PRF8962",
        "date": "2027-08-05",
        "time": "18:00",
        "side": "right",
        "notification_type": "above_zero",
    }
    payload.update(overrides)
    return services.notifications.create(
        client_id, NotificationRequest.from_payload(payload), "Advanced Surf"
    )


def run(services, argv):
    args = admin.build_parser().parse_args(argv)
    return args.handler(services, args)


def test_list_flags_notifications_that_cannot_be_delivered(services, capsys):
    add(services, "with-token")
    add(services, "without-token")
    services.clients.upsert_token("with-token", VALID_TOKEN)

    assert run(services, ["list"]) == 0
    output = capsys.readouterr().out
    assert "NO TOKEN" in output
    assert "2 notification(s)" in output
    assert VALID_TOKEN not in output


def test_list_on_an_empty_database(services, capsys):
    run(services, ["list"])
    assert "No notifications." in capsys.readouterr().out


def test_delete_client_removes_only_that_clients_notifications(services, capsys):
    add(services, "c1")
    add(services, "c1")
    add(services, "c2")

    run(services, ["delete-client", "c1"])
    assert "Deleted 2 notification(s)" in capsys.readouterr().out
    assert len(services.notifications.list_for_client("c2")) == 1


def test_delete_client_keeps_the_token_unless_asked(services):
    add(services, "c1")
    services.clients.upsert_token("c1", VALID_TOKEN)

    run(services, ["delete-client", "c1"])
    assert services.clients.get_token("c1") == VALID_TOKEN

    run(services, ["delete-client", "--token", "c1"])
    assert services.clients.get_token("c1") is None


def test_clear_thresholds_rearms_notifications(services, capsys):
    notification = add(services, "c1", notification_type="below_threshold", thresholds=[5])
    services.notifications.record_notified_thresholds(notification, [5])

    run(services, ["clear-thresholds"])
    assert "1 notification(s)" in capsys.readouterr().out
    assert services.notifications.get("c1", notification.notification_id).notified_thresholds == []


def test_prune_dry_run_changes_nothing(services, capsys):
    add(services, "orphan")

    run(services, ["prune-tokenless", "--dry-run"])
    assert "Dry run" in capsys.readouterr().out
    assert len(services.notifications.all()) == 1


def test_prune_removes_undeliverable_notifications(services):
    add(services, "orphan")
    kept = add(services, "has-token")
    services.clients.upsert_token("has-token", VALID_TOKEN)

    run(services, ["prune-tokenless"])
    remaining = services.notifications.all()
    assert [n.notification_id for n in remaining] == [kept.notification_id]


def test_prune_removes_client_rows_with_empty_tokens(services):
    with services.database.transaction() as conn:
        conn.execute("INSERT INTO clients VALUES ('blank', '', '2026-01-01', NULL)")

    run(services, ["prune-tokenless"])
    assert services.clients.count() == 0


def test_a_command_is_required():
    with pytest.raises(SystemExit):
        admin.build_parser().parse_args([])
