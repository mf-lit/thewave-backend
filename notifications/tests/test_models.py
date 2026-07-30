"""Validation and serialisation details not visible through the endpoints."""
from __future__ import annotations

import pytest

from src.models import (
    Notification,
    NotificationRequest,
    ValidationError,
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
