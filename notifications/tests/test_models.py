"""Validation and serialisation details not visible through the endpoints."""
from __future__ import annotations

import pytest

from src.models import (
    Notification,
    NotificationRequest,
    ValidationError,
    duration_hours,
    normalize_duration,
    normalize_time,
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
