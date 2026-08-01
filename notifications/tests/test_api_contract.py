"""The published HTTP contract.

Every status code and message string here was captured from the running
service before the rewrite. The mobile app is in the field and cannot be
changed in step with the server, so these are pinned, not merely asserted.
"""
from __future__ import annotations

import logging
import re
import uuid

import pytest

from src.calendar_client import CalendarError
from tests.conftest import API_KEY, VALID_TOKEN, make_day, make_performance

DATE = "2026-08-05"
PERFORMANCE_AK = "TWB.EVN6.PRF8962"


@pytest.fixture(autouse=True)
def calendar_day(calendar):
    calendar.days = [
        make_day(
            DATE,
            [
                make_performance(
                    performance_ak=PERFORMANCE_AK,
                    title="Advanced Surf",
                    time="18:00",
                    availability={"left": 14, "right": 14},
                )
            ],
        )
    ]
    return calendar


def valid_body(**overrides):
    body = {
        "performance_ak": PERFORMANCE_AK,
        "date": DATE,
        "time": "18:00",
        "side": "right",
        "notification_type": "above_zero",
    }
    body.update(overrides)
    return body


# -- health -------------------------------------------------------------------

def test_health_needs_no_auth(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


# -- authentication -----------------------------------------------------------

def test_missing_api_key(client, client_id):
    response = client.get(f"/clients/{client_id}/notifications")
    assert response.status_code == 401
    assert response.get_json() == {"error": "Missing x-api-key header"}


def test_invalid_api_key(client, client_id):
    response = client.get(
        f"/clients/{client_id}/notifications", headers={"x-api-key": "wrong"}
    )
    assert response.status_code == 401
    assert response.get_json() == {"error": "Invalid API key"}


def test_auth_is_checked_before_validation(client):
    """A bad key on a bad path still reports the key, as it always has."""
    response = client.get("/clients/not-a-uuid/notifications", headers={"x-api-key": "wrong"})
    assert response.status_code == 401
    assert response.get_json() == {"error": "Invalid API key"}


def test_auth_disabled_by_setting(services, client_id):
    from src.api.app import create_app
    from dataclasses import replace

    services.settings = replace(services.settings, auth_disabled=True)
    unauthenticated = create_app(services=services).test_client()
    assert unauthenticated.get(f"/clients/{client_id}/notifications").status_code == 200


# -- create -------------------------------------------------------------------

def test_create_returns_201_and_the_stored_shape(client, auth, client_id):
    response = client.post(
        f"/clients/{client_id}/notifications", json=valid_body(), headers=auth
    )
    assert response.status_code == 201

    body = response.get_json()
    assert set(body) == {
        "notification_id",
        "client_id",
        "performance_ak",
        "date",
        "time",
        "side",
        "title",
        "notification_type",
        "created_at",
    }
    assert body["client_id"] == client_id
    assert body["title"] == "Advanced Surf"
    assert body["side"] == "right"
    assert uuid.UUID(body["notification_id"])


def test_create_below_threshold_includes_thresholds(client, auth, client_id):
    response = client.post(
        f"/clients/{client_id}/notifications",
        json=valid_body(notification_type="below_threshold", thresholds=[5, 2]),
        headers=auth,
    )
    assert response.status_code == 201
    assert response.get_json()["thresholds"] == [5, 2]


def test_create_quiet_session_echoes_its_own_fields(client, auth, client_id):
    response = client.post(
        f"/clients/{client_id}/notifications",
        json=valid_body(
            notification_type="quiet_session", minimum_slots=12, time_before="24h"
        ),
        headers=auth,
    )
    assert response.status_code == 201

    body = response.get_json()
    assert body["notification_type"] == "quiet_session"
    assert body["minimum_slots"] == 12
    assert body["time_before"] == "24h"
    assert "thresholds" not in body


def test_the_quiet_session_fields_are_not_echoed_for_the_other_types(client, auth, client_id):
    response = client.post(
        f"/clients/{client_id}/notifications",
        json=valid_body(time_before="24h", minimum_slots=12),
        headers=auth,
    )
    assert response.status_code == 201
    body = response.get_json()
    assert "time_before" not in body and "minimum_slots" not in body


def test_create_normalises_time(client, auth, client_id):
    response = client.post(
        f"/clients/{client_id}/notifications", json=valid_body(time="18:00:00.000"), headers=auth
    )
    assert response.status_code == 201
    assert response.get_json()["time"] == "18:00"


def test_create_ignores_unknown_fields(client, auth, client_id):
    response = client.post(
        f"/clients/{client_id}/notifications",
        json=valid_body(surprise="ignored"),
        headers=auth,
    )
    assert response.status_code == 201
    assert "surprise" not in response.get_json()


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"date": "31-12-2026"}, "Invalid date format. Expected YYYY-MM-DD"),
        ({"time": "25"}, "Invalid time format. Expected HH:MM, HH:MM:SS, or HH:MM:SS.mmm"),
        ({"time": "99:00"}, "Invalid time format. Expected HH:MM, HH:MM:SS, or HH:MM:SS.mmm"),
        ({"side": "middle"}, "Invalid side. Must be 'left', 'right', or 'none'"),
        (
            {"notification_type": "maybe"},
            "Invalid notification_type. Must be 'below_threshold', "
            "'above_zero', 'quiet_session', or 'any_quiet_session'",
        ),
        ({"performance_ak": ""}, "performance_ak is required and must be a string"),
        (
            {"notification_type": "below_threshold"},
            "thresholds is required for below_threshold notification_type",
        ),
        (
            {"notification_type": "below_threshold", "thresholds": [-1]},
            "All thresholds must be non-negative integers",
        ),
        (
            {"notification_type": "below_threshold", "thresholds": ["5"]},
            "All thresholds must be non-negative integers",
        ),
        (
            {"notification_type": "quiet_session", "time_before": "24h"},
            "minimum_slots is required for quiet_session notification_type",
        ),
        (
            {"notification_type": "quiet_session", "minimum_slots": [12], "time_before": "24h"},
            "minimum_slots must be a non-negative integer",
        ),
        (
            {"notification_type": "quiet_session", "minimum_slots": -1, "time_before": "24h"},
            "minimum_slots must be a non-negative integer",
        ),
        (
            {"notification_type": "quiet_session", "minimum_slots": 12},
            "time_before is required for quiet_session notification_type",
        ),
        (
            {"notification_type": "quiet_session", "minimum_slots": 12, "time_before": 24},
            "Invalid time_before format. Expected a whole number of hours, e.g. '24h'",
        ),
        (
            {"notification_type": "quiet_session", "minimum_slots": 12, "time_before": "49h"},
            "time_before must be between 1h and 48h",
        ),
    ],
)
def test_create_validation_messages(client, auth, client_id, overrides, expected):
    response = client.post(
        f"/clients/{client_id}/notifications", json=valid_body(**overrides), headers=auth
    )
    assert response.status_code == 400
    assert response.get_json() == {"error": expected}


def test_missing_field_beats_malformed_field(client, auth, client_id):
    """Required-field checks run first, in declaration order."""
    body = valid_body(date="not-a-date")
    del body["notification_type"]
    response = client.post(f"/clients/{client_id}/notifications", json=body, headers=auth)
    assert response.status_code == 400
    assert response.get_json() == {"error": "Missing required field: notification_type"}


def test_empty_body(client, auth, client_id):
    response = client.post(f"/clients/{client_id}/notifications", json={}, headers=auth)
    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body is required"}


def test_body_without_json_content_type_is_415(client, auth, client_id):
    """Werkzeug's own 415 — preserved from the previous implementation."""
    response = client.post(f"/clients/{client_id}/notifications", headers=auth)
    assert response.status_code == 415


def test_create_bad_client_id(client, auth):
    response = client.post("/clients/not-a-uuid/notifications", json={}, headers=auth)
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid client_id format"}


def test_create_unknown_performance(client, auth, client_id):
    response = client.post(
        f"/clients/{client_id}/notifications",
        json=valid_body(performance_ak="TWB.EVN0.PRF000000"),
        headers=auth,
    )
    assert response.status_code == 404
    assert response.get_json() == {
        "error": "Performance not found for performanceAK=TWB.EVN0.PRF000000"
    }


def test_create_side_not_sold_for_performance(client, auth, client_id):
    response = client.post(
        f"/clients/{client_id}/notifications", json=valid_body(side="none"), headers=auth
    )
    assert response.status_code == 400
    assert response.get_json() == {
        "error": f"Side 'none' not found for performance {PERFORMANCE_AK}"
    }


def test_calendar_failure_does_not_leak_internals(client, auth, client_id, calendar):
    calendar.error = CalendarError("http://thewave-upstream-api:5000 refused the connection")
    response = client.post(
        f"/clients/{client_id}/notifications", json=valid_body(), headers=auth
    )
    assert response.status_code == 500
    assert response.get_json() == {"error": "Failed to fetch calendar data"}


# -- list ---------------------------------------------------------------------

def test_list_is_a_bare_array(client, auth, client_id):
    client.post(f"/clients/{client_id}/notifications", json=valid_body(), headers=auth)
    response = client.get(f"/clients/{client_id}/notifications", headers=auth)
    assert response.status_code == 200
    body = response.get_json()
    assert isinstance(body, list) and len(body) == 1


def test_list_unknown_client_is_empty(client, auth, client_id):
    response = client.get(f"/clients/{client_id}/notifications", headers=auth)
    assert response.status_code == 200
    assert response.get_json() == []


def test_list_bad_client_id(client, auth):
    response = client.get("/clients/not-a-uuid/notifications", headers=auth)
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid client_id format"}


def test_list_is_scoped_to_the_client(client, auth, client_id):
    other = str(uuid.uuid4())
    client.post(f"/clients/{client_id}/notifications", json=valid_body(), headers=auth)
    assert client.get(f"/clients/{other}/notifications", headers=auth).get_json() == []


# -- delete -------------------------------------------------------------------

def test_delete_notification(client, auth, client_id):
    created = client.post(
        f"/clients/{client_id}/notifications", json=valid_body(), headers=auth
    ).get_json()

    response = client.delete(
        f"/clients/{client_id}/notifications/{created['notification_id']}", headers=auth
    )
    assert response.status_code == 200
    assert response.get_json() == {"message": "Notification deleted"}
    assert client.get(f"/clients/{client_id}/notifications", headers=auth).get_json() == []


def test_delete_unknown_notification(client, auth, client_id):
    response = client.delete(
        f"/clients/{client_id}/notifications/{uuid.uuid4()}", headers=auth
    )
    assert response.status_code == 404
    assert response.get_json() == {"error": "Notification not found"}


def test_delete_bad_notification_id(client, auth, client_id):
    response = client.delete(f"/clients/{client_id}/notifications/nope", headers=auth)
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid notification_id format"}


def test_delete_bad_client_id(client, auth):
    response = client.delete(f"/clients/nope/notifications/{uuid.uuid4()}", headers=auth)
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid client_id format"}


def test_one_clients_delete_cannot_touch_anothers(client, auth, client_id):
    created = client.post(
        f"/clients/{client_id}/notifications", json=valid_body(), headers=auth
    ).get_json()
    other = str(uuid.uuid4())

    response = client.delete(
        f"/clients/{other}/notifications/{created['notification_id']}", headers=auth
    )
    assert response.status_code == 404
    assert len(client.get(f"/clients/{client_id}/notifications", headers=auth).get_json()) == 1


# -- fcm tokens ---------------------------------------------------------------

def test_put_token(client, auth, client_id):
    response = client.put(
        f"/clients/{client_id}/fcm-token", json={"fcm_token": VALID_TOKEN}, headers=auth
    )
    assert response.status_code == 200
    body = response.get_json()
    assert body["message"] == "FCM token saved successfully"
    assert body["updated_at"]


def test_put_token_replaces_the_previous_one(client, auth, client_id, services):
    replacement = "e" * 161
    client.put(f"/clients/{client_id}/fcm-token", json={"fcm_token": VALID_TOKEN}, headers=auth)
    client.put(f"/clients/{client_id}/fcm-token", json={"fcm_token": replacement}, headers=auth)
    assert services.clients.get_token(client_id) == replacement


def test_put_token_requires_the_field(client, auth, client_id):
    response = client.put(f"/clients/{client_id}/fcm-token", json={"other": 1}, headers=auth)
    assert response.status_code == 400
    assert response.get_json() == {"error": "fcm_token is required"}


def test_put_token_empty_body(client, auth, client_id):
    response = client.put(f"/clients/{client_id}/fcm-token", json={}, headers=auth)
    assert response.status_code == 400
    assert response.get_json() == {"error": "Request body is required"}


def test_put_token_too_short(client, auth, client_id):
    response = client.put(
        f"/clients/{client_id}/fcm-token", json={"fcm_token": "abc"}, headers=auth
    )
    assert response.status_code == 400
    assert response.get_json() == {
        "error": "FCM token length must be between 140-200 characters, got 3"
    }


def test_put_token_bad_characters(client, auth, client_id):
    response = client.put(
        f"/clients/{client_id}/fcm-token", json={"fcm_token": "a!" * 80}, headers=auth
    )
    assert response.status_code == 400
    assert response.get_json() == {
        "error": "FCM token contains invalid characters. Only alphanumeric, colon, "
        "hyphen, and underscore are allowed"
    }


def test_get_token_reports_existence_only(client, auth, client_id):
    client.put(f"/clients/{client_id}/fcm-token", json={"fcm_token": VALID_TOKEN}, headers=auth)
    response = client.get(f"/clients/{client_id}/fcm-token", headers=auth)
    assert response.status_code == 200
    assert response.get_json() == {"has_token": True}
    assert VALID_TOKEN not in response.get_data(as_text=True)


def test_get_token_missing(client, auth, client_id):
    response = client.get(f"/clients/{client_id}/fcm-token", headers=auth)
    assert response.status_code == 404
    assert response.get_json() == {"error": "FCM token not found"}


def test_delete_token(client, auth, client_id):
    client.put(f"/clients/{client_id}/fcm-token", json={"fcm_token": VALID_TOKEN}, headers=auth)
    response = client.delete(f"/clients/{client_id}/fcm-token", headers=auth)
    assert response.status_code == 200
    assert response.get_json() == {"message": "FCM token deleted successfully"}


def test_delete_token_missing(client, auth, client_id):
    response = client.delete(f"/clients/{client_id}/fcm-token", headers=auth)
    assert response.status_code == 404
    assert response.get_json() == {"error": "FCM token not found"}


def test_token_endpoints_reject_bad_client_id(client, auth):
    for method in (client.get, client.delete):
        response = method("/clients/nope/fcm-token", headers=auth)
        assert response.status_code == 400
        assert response.get_json() == {"error": "Invalid client_id format"}


# -- logging ------------------------------------------------------------------

ALERT_PATTERN = re.compile(r"error|exception|fatal|panic", re.IGNORECASE)


def alerting_lines(caplog):
    """Log lines that log-alerts would page on.

    It greps container stdout, so match against the same rendered line the
    container emits — level name and logger name included, not just the
    message.
    """
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    return [
        line
        for line in (formatter.format(record) for record in caplog.records)
        if ALERT_PATTERN.search(line)
    ]


def test_client_mistakes_do_not_trip_the_log_alerter(client, auth, client_id, caplog):
    """A user typing a bad request is not an incident worth a phone alert."""
    with caplog.at_level(logging.DEBUG):
        client.get("/clients/not-a-uuid/notifications", headers=auth)
        client.get(f"/clients/{client_id}/fcm-token", headers=auth)
        client.post(f"/clients/{client_id}/notifications", json={}, headers=auth)
        client.get(f"/clients/{client_id}/notifications", headers={"x-api-key": "wrong"})
        client.delete(f"/clients/{client_id}/notifications/{uuid.uuid4()}", headers=auth)
        client.put(f"/clients/{client_id}/fcm-token", json={"fcm_token": "x"}, headers=auth)

    assert alerting_lines(caplog) == []


def test_a_genuine_failure_still_alerts(client, auth, client_id, calendar, caplog):
    calendar.error = CalendarError("upstream is down")
    with caplog.at_level(logging.DEBUG):
        client.post(f"/clients/{client_id}/notifications", json=valid_body(), headers=auth)

    assert alerting_lines(caplog)


# -- key rotation -------------------------------------------------------------

def test_rotated_api_key_is_picked_up_without_restart(client, client_id, config_path):
    import yaml

    rotated = "rotated-key-9876543210"
    config_path.write_text(yaml.safe_dump({"api_keys": [rotated]}))

    assert client.get(
        f"/clients/{client_id}/notifications", headers={"x-api-key": API_KEY}
    ).status_code == 401
    assert client.get(
        f"/clients/{client_id}/notifications", headers={"x-api-key": rotated}
    ).status_code == 200


# -- any_quiet_session --------------------------------------------------------

def rolling_body(**overrides):
    body = {
        "notification_type": "any_quiet_session",
        "title": "Advanced Surf",
        "side": "right",
        "minimum_slots": 8,
        "time_before": "24h",
    }
    body.update(overrides)
    return body


def test_a_rolling_watch_is_created_without_naming_a_session(client, client_id, auth):
    response = client.post(
        f"/clients/{client_id}/notifications", json=rolling_body(), headers=auth
    )

    assert response.status_code == 201
    body = response.get_json()
    assert body["notification_type"] == "any_quiet_session"
    assert body["title"] == "Advanced Surf"
    assert body["minimum_slots"] == 8 and body["time_before"] == "24h"
    assert body["performance_ak"] == "" and body["date"] == "" and body["time"] == ""


def test_creating_a_rolling_watch_does_not_touch_the_calendar(
    client, client_id, auth, calendar
):
    """It names a title, not a session, so there is nothing to look up."""
    client.post(f"/clients/{client_id}/notifications", json=rolling_body(), headers=auth)

    assert calendar.requested_dates == []


def test_a_rolling_watch_can_be_created_while_the_calendar_is_down(
    client, client_id, auth, calendar
):
    calendar.error = CalendarError("upstream is down")

    response = client.post(
        f"/clients/{client_id}/notifications", json=rolling_body(), headers=auth
    )

    assert response.status_code == 201


def test_an_unscheduled_title_is_accepted(client, client_id, auth):
    """Titles come and go seasonally; the row outlives any one of them."""
    response = client.post(
        f"/clients/{client_id}/notifications",
        json=rolling_body(title="Midnight Longboarding"),
        headers=auth,
    )

    assert response.status_code == 201
    assert response.get_json()["title"] == "Midnight Longboarding"


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"title": ""}, "title is required and must be a string"),
        ({"side": "middle"}, "Invalid side. Must be 'left', 'right', or 'none'"),
        ({"minimum_slots": -1}, "minimum_slots must be a non-negative integer"),
        ({"time_before": "72h"}, "time_before must be between 1h and 48h"),
    ],
)
def test_rolling_validation_messages(client, client_id, auth, overrides, expected):
    response = client.post(
        f"/clients/{client_id}/notifications", json=rolling_body(**overrides), headers=auth
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": expected}


@pytest.mark.parametrize("field", ["title", "minimum_slots", "time_before"])
def test_rolling_required_fields(client, client_id, auth, field):
    body = rolling_body()
    del body[field]

    response = client.post(f"/clients/{client_id}/notifications", json=body, headers=auth)

    assert response.status_code == 400
    assert field in response.get_json()["error"]


def test_a_rolling_watch_round_trips_through_the_list_endpoint(client, client_id, auth):
    client.post(f"/clients/{client_id}/notifications", json=rolling_body(), headers=auth)

    listed = client.get(f"/clients/{client_id}/notifications", headers=auth).get_json()

    assert len(listed) == 1
    assert listed[0]["notification_type"] == "any_quiet_session"
    assert "notified_performances" not in listed[0]
