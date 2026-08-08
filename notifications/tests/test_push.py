"""The FCM payload is a contract with the shipped mobile app.

The Flutter client reads these data keys and iOS renders this title/body when
the app is backgrounded, so changing any string here requires an app release.
"""
from __future__ import annotations

from dataclasses import replace

import logging

import pytest

from src.models import (
    ABOVE_ZERO,
    ANY_QUIET_SESSION,
    BELOW_THRESHOLD,
    QUIET_SESSION,
    Notification,
)
from src.push import (
    Notifier,
    PushError,
    TokenRejected,
    data_payload,
    display_strings,
    fingerprint,
    format_date_short,
    side_phrase,
)
from tests.conftest import VALID_TOKEN


def notification(**overrides) -> Notification:
    base = dict(
        client_id="client-1",
        notification_id="notif-1",
        performance_ak="TWB.EVN6.PRF8962",
        date="2026-01-05",
        time="18:00",
        side="right",
        title="Advanced Surf",
        notification_type=BELOW_THRESHOLD,
        created_at="2026-07-30T12:00:00",
        thresholds=[5],
        notified_thresholds=[],
    )
    base.update(overrides)
    return Notification(**base)


# -- formatting ---------------------------------------------------------------

@pytest.mark.parametrize(
    "date, expected",
    [
        ("2026-01-01", "1st Jan"),
        ("2026-01-02", "2nd Jan"),
        ("2026-01-03", "3rd Jan"),
        ("2026-01-04", "4th Jan"),
        ("2026-01-11", "11th Jan"),
        ("2026-01-12", "12th Jan"),
        ("2026-01-13", "13th Jan"),
        ("2026-01-21", "21st Jan"),
        ("2026-12-22", "22nd Dec"),
        ("2026-09-23", "23rd Sep"),
        ("not-a-date", "not-a-date"),
    ],
)
def test_short_date_formatting(date, expected):
    assert format_date_short(date) == expected


def test_threshold_display_strings():
    title, body = display_strings(notification(), 3)
    assert title == "Advanced Surf: 5th Jan at 18:00"
    assert body == "Availability dropped to 3 on the right"


def test_above_zero_display_strings():
    title, body = display_strings(notification(notification_type=ABOVE_ZERO), 2)
    assert title == "Advanced Surf: 5th Jan at 18:00"
    assert body == "A session has become available"


def test_quiet_session_display_strings():
    """Without its own branch this would inherit the below_threshold wording."""
    title, body = display_strings(notification(notification_type=QUIET_SESSION), 12)
    assert title == "Advanced Surf: 5th Jan at 18:00"
    assert body == "Possible quiet session: 12 slots remaining on the right"


def test_quiet_session_sends_its_minimum_in_its_own_key():
    """`threshold` fires at or below; this fires at or above. Separate keys."""
    payload = data_payload(
        notification(notification_type=QUIET_SESSION, minimum_slots=12), 20, None
    )
    assert payload["notification_type"] == "quiet_session"
    assert payload["minimum_slots"] == "12"
    assert payload["threshold"] == ""
    assert payload["availability"] == "20"


def test_a_zero_minimum_is_not_confused_with_an_absent_one():
    absent = data_payload(notification(), 3, 5)
    zero = data_payload(notification(notification_type=QUIET_SESSION, minimum_slots=0), 3, None)
    assert absent["minimum_slots"] == ""
    assert zero["minimum_slots"] == "0"


def test_untitled_session_falls_back():
    title, _ = display_strings(notification(title=""), 1)
    assert title == "Session: 5th Jan at 18:00"


def test_data_payload_keys_and_types():
    payload = data_payload(notification(), 3, 5)
    assert payload == {
        "performance_ak": "TWB.EVN6.PRF8962",
        "date": "2026-01-05",
        "time": "18:00",
        "side": "right",
        "session_title": "Advanced Surf",
        "availability": "3",
        "notification_type": "below_threshold",
        "notification_id": "notif-1",
        "threshold": "5",
        "minimum_slots": "",
    }
    assert all(isinstance(value, str) for value in payload.values())


def test_absent_threshold_is_an_empty_string_not_none():
    assert data_payload(notification(notification_type=ABOVE_ZERO), 3, None)["threshold"] == ""


def test_zero_threshold_is_not_confused_with_absent():
    assert data_payload(notification(), 0, 0)["threshold"] == "0"


# -- token handling -----------------------------------------------------------

def test_fingerprint_hides_the_token():
    printed = fingerprint(VALID_TOKEN)
    assert VALID_TOKEN not in printed
    assert len(printed) < 20


def test_notifier_sends_to_the_stored_token(services, sender):
    services.clients.upsert_token("client-1", VALID_TOKEN)
    assert services.notifier.send(notification(), 3, 5) is True

    (message,) = sender.sent
    assert message.token == VALID_TOKEN
    assert message.title == "Advanced Surf: 5th Jan at 18:00"
    assert message.data["threshold"] == "5"


def test_notifier_skips_clients_without_a_token(services, sender):
    assert services.notifier.send(notification(), 3, 5) is False
    assert sender.sent == []


def test_rejected_token_is_deleted(services, sender):
    services.clients.upsert_token("client-1", VALID_TOKEN)
    sender.error = TokenRejected("Requested entity was not found")

    assert services.notifier.send(notification(), 3, 5) is False
    assert services.clients.get_token("client-1") is None


def test_transient_failure_keeps_the_token(services, sender):
    services.clients.upsert_token("client-1", VALID_TOKEN)
    sender.error = PushError("backend unavailable")

    assert services.notifier.send(notification(), 3, 5) is False
    assert services.clients.get_token("client-1") == VALID_TOKEN


def test_the_token_is_never_written_to_the_log(services, sender, caplog):
    services.clients.upsert_token("client-1", VALID_TOKEN)
    with caplog.at_level(logging.DEBUG):
        services.notifier.send(notification(), 3, 5)
    assert VALID_TOKEN not in caplog.text


def test_a_rejected_token_is_not_logged_either(services, sender, caplog):
    services.clients.upsert_token("client-1", VALID_TOKEN)
    sender.error = TokenRejected("gone")
    with caplog.at_level(logging.DEBUG):
        services.notifier.send(notification(), 3, 5)
    assert VALID_TOKEN not in caplog.text


def test_notifier_uses_a_fake_sender_only(sender):
    """Guard against the suite ever reaching the real Firebase SDK."""
    assert type(sender).__name__ == "FakeSender"


def test_any_quiet_session_shares_the_quiet_session_body():
    """One sentence for both: the title line already names the session."""
    title, body = display_strings(notification(notification_type=ANY_QUIET_SESSION), 12)
    assert body == "Possible quiet session: 12 slots remaining on the right"


def test_a_rolling_push_describes_the_matched_session():
    """The worker hands the notifier a copy carrying the session's identity."""
    row = notification(
        notification_type=ANY_QUIET_SESSION, performance_ak="", date="", time=""
    )
    matched = replace(row, performance_ak="P9", date="2026-01-05", time="18:00")

    title, _ = display_strings(matched, 12)
    assert title == "Advanced Surf: 5th Jan at 18:00"
    assert data_payload(matched, 12, None)["performance_ak"] == "P9"


# -- sessions with no side ----------------------------------------------------

@pytest.mark.parametrize("side, expected", [("left", " on the left"), ("right", " on the right")])
def test_side_phrase_names_a_real_side(side, expected):
    assert side_phrase(side) == expected


@pytest.mark.parametrize("side", ["none", "", None])
def test_side_phrase_is_empty_when_there_is_no_side(side):
    """'none' is a stored value, not a missing one, and it is not a place."""
    assert side_phrase(side) == ""


@pytest.mark.parametrize(
    "notification_type, expected",
    [
        (QUIET_SESSION, "Possible quiet session: 12 slots remaining"),
        (ANY_QUIET_SESSION, "Possible quiet session: 12 slots remaining"),
        (BELOW_THRESHOLD, "Availability dropped to 12"),
    ],
)
def test_a_whole_lagoon_session_does_not_say_on_the_none(notification_type, expected):
    """Beginner Lesson, Play In The Bay and Pilates only ever sell side 'none'."""
    _, body = display_strings(
        notification(notification_type=notification_type, side="none"), 12
    )
    assert body == expected
    assert "none" not in body


def test_a_sided_session_still_names_its_side():
    """The clause is dropped only where it had nothing to say."""
    _, quiet = display_strings(notification(notification_type=QUIET_SESSION, side="left"), 12)
    _, dropped = display_strings(notification(notification_type=BELOW_THRESHOLD, side="left"), 3)
    assert quiet == "Possible quiet session: 12 slots remaining on the left"
    assert dropped == "Availability dropped to 3 on the left"


def test_the_side_is_still_sent_verbatim_in_the_data_map():
    """Display copy changed; the key the Flutter app parses did not."""
    assert data_payload(notification(side="none"), 12, None)["side"] == "none"
