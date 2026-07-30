"""Domain models and request validation.

The error strings in this module are part of the API contract — the mobile
app surfaces some of them directly — so they are reproduced verbatim from the
previous implementation, as is the order in which the checks run (the first
failure wins, and a missing field beats a malformed one).
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

BELOW_THRESHOLD = "below_threshold"
ABOVE_ZERO = "above_zero"

VALID_SIDES: Tuple[str, ...] = ("left", "right", "none")
VALID_NOTIFICATION_TYPES: Tuple[str, ...] = (BELOW_THRESHOLD, ABOVE_ZERO)

REQUIRED_FIELDS: Tuple[str, ...] = (
    "performance_ak",
    "date",
    "time",
    "side",
    "notification_type",
)

TIME_FORMAT_ERROR = "Invalid time format. Expected HH:MM, HH:MM:SS, or HH:MM:SS.mmm"


class ValidationError(ValueError):
    """A client-visible validation failure. ``str(exc)`` is the API message."""


def normalize_time(value: Any) -> str:
    """Accept HH:MM, HH:MM:SS or HH:MM:SS.mmm; return canonical HH:MM."""
    if not isinstance(value, str):
        raise ValidationError(TIME_FORMAT_ERROR)
    try:
        parts = value.split(":")
        if len(parts) < 2:
            raise ValidationError(TIME_FORMAT_ERROR)
        hour = int(parts[0])
        minute = int(parts[1].split(".")[0])
    except (ValueError, IndexError):
        raise ValidationError(TIME_FORMAT_ERROR)

    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValidationError(TIME_FORMAT_ERROR)
    return f"{hour:02d}:{minute:02d}"


@dataclass(frozen=True)
class NotificationRequest:
    """A validated ``POST /clients/<id>/notifications`` body."""

    performance_ak: str
    date: str
    time: str
    side: str
    notification_type: str
    thresholds: Optional[List[int]] = None

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "NotificationRequest":
        for name in REQUIRED_FIELDS:
            if name not in payload:
                raise ValidationError(f"Missing required field: {name}")

        performance_ak = payload["performance_ak"]
        if not performance_ak or not isinstance(performance_ak, str):
            raise ValidationError("performance_ak is required and must be a string")

        date = payload["date"]
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except (ValueError, TypeError):
            raise ValidationError("Invalid date format. Expected YYYY-MM-DD")

        time = normalize_time(payload["time"])

        side = payload["side"]
        if side not in VALID_SIDES:
            raise ValidationError("Invalid side. Must be 'left', 'right', or 'none'")

        notification_type = payload["notification_type"]
        if notification_type not in VALID_NOTIFICATION_TYPES:
            raise ValidationError(
                "Invalid notification_type. Must be 'below_threshold' or 'above_zero'"
            )

        thresholds = payload.get("thresholds")
        if notification_type == BELOW_THRESHOLD:
            if not thresholds:
                raise ValidationError(
                    "thresholds is required for below_threshold notification_type"
                )
            if not all(isinstance(t, int) and t >= 0 for t in thresholds):
                raise ValidationError("All thresholds must be non-negative integers")
        else:
            thresholds = None

        return cls(
            performance_ak=performance_ak,
            date=date,
            time=time,
            side=side,
            notification_type=notification_type,
            thresholds=list(thresholds) if thresholds else None,
        )


def _decode_json_list(raw: Any) -> Optional[List[int]]:
    """Decode a JSON-text column, tolerating the NULLs and ``[]`` in prod."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class Notification:
    """A stored notification row."""

    client_id: str
    notification_id: str
    performance_ak: str
    date: str
    time: str
    side: str
    title: str
    notification_type: str
    created_at: str
    thresholds: Optional[List[int]] = None
    notified_thresholds: List[int] = field(default_factory=list)
    last_checked_availability: Optional[int] = None
    next_check_at: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Notification":
        return cls(
            client_id=row["client_id"],
            notification_id=row["notification_id"],
            performance_ak=row["performance_ak"],
            date=row["date"],
            time=row["time"],
            side=row["side"],
            title=row["title"],
            notification_type=row["notification_type"],
            created_at=row["created_at"],
            thresholds=_decode_json_list(row["thresholds"]),
            notified_thresholds=_decode_json_list(row["notified_thresholds"]) or [],
            last_checked_availability=row["last_checked_availability"],
            next_check_at=row["next_check_at"],
        )

    def to_api(self) -> Dict[str, Any]:
        """The wire shape clients receive.

        ``thresholds`` appears only for below_threshold notifications and
        ``last_checked_availability`` only once a check has run, matching
        what the app has always been sent.
        """
        body: Dict[str, Any] = {
            "notification_id": self.notification_id,
            "client_id": self.client_id,
            "performance_ak": self.performance_ak,
            "date": self.date,
            "time": self.time,
            "side": self.side,
            "title": self.title,
            "notification_type": self.notification_type,
            "created_at": self.created_at,
        }
        if self.thresholds is not None:
            body["thresholds"] = self.thresholds
        if self.last_checked_availability is not None:
            body["last_checked_availability"] = self.last_checked_availability
        return body
