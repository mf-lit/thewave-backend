"""Validation and serialisation details not visible through the endpoints."""
from __future__ import annotations

import re

import pytest

from src.models import (
    Notification,
    NotificationRequest,
    ValidationError,
    _decode_json_map,
    duration_hours,
    normalize_duration,
    normalize_time,
    normalize_title,
)


@pytest.mark.parametrize(
    "given, expected",
    [
        ("18:00", "18:00"),
        ("18:00:00", "18:00"),
        ("18:00:00.000", "18:00"),
        ("9:05", "09:05"),
        ("00:00", "00:00"),
        ("23:59", "23:59"),
    ],
)
def test_accepted_time_formats(given, expected):
    assert normalize_time(given) == expected


@pytest.mark.parametrize("given", ["18", "", "24:00", "18:60", "-1:00", "aa:bb", None, 1800])
def test_rejected_time_formats(given):
    with pytest.raises(ValidationError):
        normalize_time(given)


# -- time_before durations ----------------------------------------------------

@pytest.mark.parametrize(
    "given, expected",
    [("24h", "24h"), ("24H", "24h"), (" 24h ", "24h"), ("1h", "1h"), ("48h", "48h"),
     ("06h", "6h")],
)
def test_accepted_duration_formats(given, expected):
    assert normalize_duration(given) == expected


@pytest.mark.parametrize("given", ["24", "h", "", "24 h", "1.5h", "-1h", None, 24, ["24h"]])
def test_rejected_duration_formats(given):
    """The unit suffix is mandatory, so 30m or 2d can be added later."""
    with pytest.raises(ValidationError, match="Expected a whole number of hours"):
        normalize_duration(given)


@pytest.mark.parametrize("given", ["0h", "49h", "100h"])
def test_durations_outside_the_supported_window_are_rejected(given):
    with pytest.raises(ValidationError, match="between 1h and 48h"):
        normalize_duration(given)


def test_duration_hours_reads_a_stored_value():
    assert duration_hours("24h") == 24


@pytest.mark.parametrize("given", [None, "", "24", "later", 24])
def test_duration_hours_tolerates_whatever_is_in_the_column(given):
    """The read side never raises; a broken row must not stall the worker."""
    assert duration_hours(given) is None


def valid_payload(**overrides):
    payload = {
        "performance_ak": "TWB.EVN6.PRF8962",
        "date": "2026-08-05",
        "time": "18:00",
        "side": "right",
        "notification_type": "above_zero",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("field", ["performance_ak", "date", "time", "side", "notification_type"])
def test_every_required_field_is_named_in_its_error(field):
    payload = valid_payload()
    del payload[field]
    with pytest.raises(ValidationError, match=f"Missing required field: {field}"):
        NotificationRequest.from_payload(payload)


def test_required_fields_are_reported_in_declaration_order():
    with pytest.raises(ValidationError, match="Missing required field: performance_ak"):
        NotificationRequest.from_payload({})


def test_thresholds_are_dropped_for_above_zero():
    """A client sending both must not end up with stored thresholds."""
    request = NotificationRequest.from_payload(valid_payload(thresholds=[5]))
    assert request.thresholds is None


def test_thresholds_are_copied_not_aliased():
    thresholds = [5, 2]
    request = NotificationRequest.from_payload(
        valid_payload(notification_type="below_threshold", thresholds=thresholds)
    )
    thresholds.append(99)
    assert request.thresholds == [5, 2]


def test_side_none_is_valid():
    assert NotificationRequest.from_payload(valid_payload(side="none")).side == "none"


def stored(**overrides) -> Notification:
    base = dict(
        client_id="c1",
        notification_id="n1",
        performance_ak="ak",
        date="2026-08-05",
        time="18:00",
        side="right",
        title="Advanced Surf",
        notification_type="above_zero",
        created_at="2026-07-30T12:00:00.000001",
    )
    base.update(overrides)
    return Notification(**base)


def test_optional_fields_are_omitted_until_they_have_values():
    body = stored().to_api()
    assert "thresholds" not in body
    assert "last_checked_availability" not in body


def test_a_zero_reading_is_still_reported():
    assert stored(last_checked_availability=0).to_api()["last_checked_availability"] == 0


def test_internal_columns_are_not_exposed():
    body = stored(next_check_at="2026-07-30 19:00:00", notified_thresholds=[5]).to_api()
    assert "next_check_at" not in body
    assert "notified_thresholds" not in body


# -- quiet_session ------------------------------------------------------------

def quiet_payload(**overrides):
    fields = {"notification_type": "quiet_session", "minimum_slots": 12, "time_before": "24h"}
    fields.update(overrides)
    return valid_payload(**fields)


def test_quiet_session_keeps_its_minimum_and_duration():
    request = NotificationRequest.from_payload(quiet_payload())
    assert request.minimum_slots == 12
    assert request.time_before == "24h"


def test_quiet_session_does_not_borrow_the_thresholds_column():
    """Thresholds mean 'at or below'; this type fires at or above."""
    request = NotificationRequest.from_payload(quiet_payload(thresholds=[5]))
    assert request.thresholds is None


def test_quiet_session_stores_the_canonical_duration():
    assert NotificationRequest.from_payload(quiet_payload(time_before="6H")).time_before == "6h"


def test_quiet_session_requires_minimum_slots():
    payload = quiet_payload()
    del payload["minimum_slots"]
    with pytest.raises(
        ValidationError, match="minimum_slots is required for quiet_session notification_type"
    ):
        NotificationRequest.from_payload(payload)


@pytest.mark.parametrize("given", [-1, "12", [12], 1.5, None])
def test_quiet_session_rejects_a_malformed_minimum(given):
    with pytest.raises(ValidationError, match="minimum_slots must be a non-negative integer"):
        NotificationRequest.from_payload(quiet_payload(minimum_slots=given))


def test_quiet_session_requires_time_before():
    payload = quiet_payload()
    del payload["time_before"]
    with pytest.raises(
        ValidationError, match="time_before is required for quiet_session notification_type"
    ):
        NotificationRequest.from_payload(payload)


def test_the_quiet_session_fields_are_dropped_for_the_other_types():
    """A client sending them on a polled type must not have them stored."""
    request = NotificationRequest.from_payload(
        valid_payload(time_before="24h", minimum_slots=12)
    )
    assert request.time_before is None
    assert request.minimum_slots is None


def test_the_quiet_session_fields_are_omitted_from_the_api_shape_until_set():
    body = stored().to_api()
    assert "time_before" not in body and "minimum_slots" not in body

    quiet = stored(minimum_slots=12, time_before="24h").to_api()
    assert quiet["minimum_slots"] == 12 and quiet["time_before"] == "24h"


def test_a_zero_minimum_is_still_reported():
    """0 is a legitimate minimum and must not be dropped as falsy."""
    assert stored(minimum_slots=0).to_api()["minimum_slots"] == 0


# -- any_quiet_session --------------------------------------------------------

def rolling_payload(**overrides):
    payload = {
        "notification_type": "any_quiet_session",
        "title": "Advanced Surf",
        "side": "right",
        "minimum_slots": 8,
        "time_before": "24h",
    }
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("field", ["title", "side", "notification_type"])
def test_any_quiet_session_names_its_own_required_fields(field):
    payload = rolling_payload()
    del payload[field]
    if field == "notification_type":
        # Without the type there is nothing to divert on, so it falls back to
        # the fields every other type requires.
        expected = "Missing required field: performance_ak"
    else:
        expected = f"Missing required field: {field}"
    with pytest.raises(ValidationError, match=expected):
        NotificationRequest.from_payload(payload)


@pytest.mark.parametrize("field", ["performance_ak", "date", "time"])
def test_any_quiet_session_requires_none_of_the_session_fields(field):
    """It names a kind of session, so the three that identify one are absent."""
    request = NotificationRequest.from_payload(rolling_payload())
    assert getattr(request, field) == ""


def test_any_quiet_session_ignores_session_fields_that_are_sent_anyway():
    request = NotificationRequest.from_payload(
        rolling_payload(performance_ak="TWB.EVN1.PRF1", date="2026-08-05", time="18:00")
    )
    assert (request.performance_ak, request.date, request.time) == ("", "", "")


def test_any_quiet_session_keeps_its_title():
    assert NotificationRequest.from_payload(rolling_payload()).title == "Advanced Surf"


def test_a_rolling_title_is_stripped():
    request = NotificationRequest.from_payload(rolling_payload(title="  Advanced Surf \n"))
    assert request.title == "Advanced Surf"


@pytest.mark.parametrize("given", ["", "   ", None, 5, []])
def test_a_rolling_title_must_be_a_non_empty_string(given):
    payload = rolling_payload(title=given)
    if given == "   ":
        # Whitespace is a string and passes the type check; it is stripped to
        # nothing, which `matching_sessions` then refuses to match on.
        assert NotificationRequest.from_payload(payload).title == ""
        return
    with pytest.raises(ValidationError, match="title is required and must be a string"):
        NotificationRequest.from_payload(payload)


def test_thresholds_are_dropped_for_any_quiet_session():
    request = NotificationRequest.from_payload(rolling_payload(thresholds=[5]))
    assert request.thresholds is None


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"side": "middle"}, "Invalid side. Must be 'left', 'right', or 'none'"),
        ({"minimum_slots": None}, "minimum_slots must be a non-negative integer"),
        ({"minimum_slots": -1}, "minimum_slots must be a non-negative integer"),
        ({"minimum_slots": "8"}, "minimum_slots must be a non-negative integer"),
        ({"time_before": "24"}, "Invalid time_before format"),
        ({"time_before": "72h"}, "time_before must be between 1h and 48h"),
        ({"time_before": "0h"}, "time_before must be between 1h and 48h"),
    ],
)
def test_any_quiet_session_validation_messages(overrides, expected):
    with pytest.raises(ValidationError, match=re.escape(expected)):
        NotificationRequest.from_payload(rolling_payload(**overrides))


@pytest.mark.parametrize("field", ["minimum_slots", "time_before"])
def test_any_quiet_session_reports_its_missing_optionals_by_name(field):
    payload = rolling_payload()
    del payload[field]
    with pytest.raises(
        ValidationError, match=f"{field} is required for any_quiet_session notification_type"
    ):
        NotificationRequest.from_payload(payload)


def test_the_session_fields_are_emitted_empty_rather_than_omitted():
    """The response shape stays invariant across types, so clients can't miss a key."""
    body = stored(
        notification_type="any_quiet_session", performance_ak="", date="", time=""
    ).to_api()
    assert body["performance_ak"] == "" and body["date"] == "" and body["time"] == ""


def test_notified_performances_is_internal():
    body = stored(notified_performances={"P1": "2026-08-05"}).to_api()
    assert "notified_performances" not in body


# -- normalize_title ----------------------------------------------------------

@pytest.mark.parametrize(
    "given, expected",
    [
        ("Advanced Surf", "advanced surf"),
        ("  Advanced Surf  ", "advanced surf"),
        ("ADVANCED SURF", "advanced surf"),
        (None, ""),
        (5, ""),
    ],
)
def test_normalize_title_folds_case_and_surrounding_space(given, expected):
    assert normalize_title(given) == expected


@pytest.mark.parametrize(
    "other",
    [
        "Advanced Surf Lesson",
        "Advanced Coaching (In Water)",
        "Advanced Plus Surf",
    ],
)
def test_normalize_title_keeps_neighbouring_titles_distinct(other):
    """Nothing inside a title is rewritten, so no session type can leak in."""
    assert normalize_title(other) != normalize_title("Advanced Surf")


@pytest.mark.parametrize(
    "shorter, longer",
    [
        ("Intermediate Plus", "Intermediate Plus (With Lesson)"),
        ("Performance Coaching (ADV+)", "Performance Coaching (EXP T)"),
        ("Intermediate Surf", "Intermediate Surf Lesson"),
    ],
)
def test_parenthesised_qualifiers_are_load_bearing(shorter, longer):
    """These pairs are real upstream titles that must never compare equal."""
    assert normalize_title(shorter) != normalize_title(longer)


# -- _decode_json_map ---------------------------------------------------------

@pytest.mark.parametrize("given", [None, "", "junk", "[1, 2]", "5", 5, "null"])
def test_decode_json_map_returns_an_empty_map_for_anything_unusable(given):
    assert _decode_json_map(given) == {}


def test_decode_json_map_round_trips_and_coerces_to_strings():
    assert _decode_json_map('{"P1": "2026-08-05"}') == {"P1": "2026-08-05"}
    assert _decode_json_map({"P1": 2026}) == {"P1": "2026"}
