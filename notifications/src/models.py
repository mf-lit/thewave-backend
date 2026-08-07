"""Domain models and request validation.

The error strings in this module are part of the API contract — the mobile
app surfaces some of them directly — so they are reproduced verbatim from the
previous implementation, as is the order in which the checks run (the first
failure wins, and a missing field beats a malformed one).
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

BELOW_THRESHOLD = "below_threshold"
ABOVE_ZERO = "above_zero"
QUIET_SESSION = "quiet_session"
ANY_QUIET_SESSION = "any_quiet_session"

VALID_SIDES: Tuple[str, ...] = ("left", "right", "none")

# Ordered so the index *is* `datetime.weekday()` — Monday is 0. `clock` relies
# on that to name a session's day with a single lookup.
VALID_DAYS: Tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

# The optional filters any_quiet_session accepts and no other type does.
FILTER_FIELDS: Tuple[str, ...] = ("days", "not_before", "not_after")

VALID_NOTIFICATION_TYPES: Tuple[str, ...] = (
    BELOW_THRESHOLD,
    ABOVE_ZERO,
    QUIET_SESSION,
    ANY_QUIET_SESSION,
)

REQUIRED_FIELDS: Tuple[str, ...] = (
    "performance_ak",
    "date",
    "time",
    "side",
    "notification_type",
)

# any_quiet_session names a *kind* of session rather than one session, so it
# requires none of the three fields that identify one and requires a title,
# which for every other type is read off the calendar instead of the payload.
ANY_QUIET_REQUIRED_FIELDS: Tuple[str, ...] = (
    "title",
    "side",
    "notification_type",
)

TIME_FORMAT_ERROR = "Invalid time format. Expected HH:MM, HH:MM:SS, or HH:MM:SS.mmm"

DAYS_FORMAT_ERROR = (
    "Invalid days. Expected a non-empty list of 'mon', 'tue', 'wed', 'thu', "
    "'fri', 'sat' or 'sun'"
)

MAX_TIME_BEFORE_HOURS = 48
TIME_BEFORE_FORMAT_ERROR = (
    "Invalid time_before format. Expected a whole number of hours, e.g. '24h'"
)

# Only hours today. The suffix is required so minutes or days can be added
# later without reinterpreting values already stored.
_DURATION = re.compile(r"^(\d+)h$")


class ValidationError(ValueError):
    """A client-visible validation failure. ``str(exc)`` is the API message."""


def normalize_time(value: Any, field: Optional[str] = None) -> str:
    """Accept HH:MM, HH:MM:SS or HH:MM:SS.mmm; return canonical HH:MM.

    ``field`` names the offending field in the error. Without it the message is
    the one the API has always returned for ``time``, verbatim; a payload
    carrying more than one time-valued field passes its name so the client can
    tell which of them it got wrong.
    """
    error = TIME_FORMAT_ERROR if field is None else TIME_FORMAT_ERROR.replace(
        "Invalid time format", f"Invalid {field} format", 1
    )
    if not isinstance(value, str):
        raise ValidationError(error)
    try:
        parts = value.split(":")
        if len(parts) < 2:
            raise ValidationError(error)
        hour = int(parts[0])
        minute = int(parts[1].split(".")[0])
    except (ValueError, IndexError):
        raise ValidationError(error)

    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValidationError(error)
    return f"{hour:02d}:{minute:02d}"


def normalize_days(value: Any) -> List[str]:
    """Accept a list of day names in any case; return them canonical and sorted.

    Sorted into calendar order and deduplicated, so ``["SUN", "sat", "sun"]``
    and ``["sat", "sun"]`` produce the same stored value and two watches for the
    same weekends are the same row to read.

    An empty list is rejected rather than read as "every day": omitting the
    field already spells that, so a list that arrived empty is far more likely a
    client that built it from an empty selection and would otherwise create a
    watch that can never fire.
    """
    if not isinstance(value, list) or not value:
        raise ValidationError(DAYS_FORMAT_ERROR)

    wanted = set()
    for day in value:
        if not isinstance(day, str):
            raise ValidationError(DAYS_FORMAT_ERROR)
        name = day.strip().lower()
        if name not in VALID_DAYS:
            raise ValidationError(DAYS_FORMAT_ERROR)
        wanted.add(name)
    return [day for day in VALID_DAYS if day in wanted]


def normalize_duration(value: Any) -> str:
    """Accept ``24h`` (or ``24H``); return canonical ``24h``."""
    if not isinstance(value, str):
        raise ValidationError(TIME_BEFORE_FORMAT_ERROR)
    match = _DURATION.match(value.strip().lower())
    if not match:
        raise ValidationError(TIME_BEFORE_FORMAT_ERROR)

    hours = int(match.group(1))
    if not 1 <= hours <= MAX_TIME_BEFORE_HOURS:
        raise ValidationError(
            f"time_before must be between 1h and {MAX_TIME_BEFORE_HOURS}h"
        )
    return f"{hours}h"


def normalize_title(value: Any) -> str:
    """Fold a session title to its comparable form.

    Case and surrounding whitespace only. Nothing inside the title is
    rewritten: the parenthesised parts upstream uses — (In Water),
    (With Lesson), (ADV+), (EXP T) — are what tell two session types apart, so
    a watch for "Advanced Surf" must never match "Advanced Surf Lesson" or
    "Advanced Coaching (In Water)".
    """
    return value.strip().lower() if isinstance(value, str) else ""


def duration_hours(value: Any) -> Optional[int]:
    """The hour count in a stored ``time_before``, or None if unreadable.

    The read-side counterpart to `normalize_duration`: it tolerates whatever is
    actually in the column rather than raising, as `_decode_json_list` does.
    """
    if not isinstance(value, str):
        return None
    match = _DURATION.match(value.strip().lower())
    return int(match.group(1)) if match else None


@dataclass(frozen=True)
class NotificationRequest:
    """A validated ``POST /clients/<id>/notifications`` body."""

    performance_ak: str
    date: str
    time: str
    side: str
    notification_type: str
    thresholds: Optional[List[int]] = None
    minimum_slots: Optional[int] = None
    time_before: Optional[str] = None
    title: Optional[str] = None
    days: Optional[List[str]] = None
    not_before: Optional[str] = None
    not_after: Optional[str] = None

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "NotificationRequest":
        # The type decides which fields are required, so it has to be read
        # before the presence loop. Only an exact match with the new constant
        # diverts: every other payload — valid, malformed, or missing
        # notification_type entirely — reaches the checks that have always run,
        # in the order they have always run in.
        notification_type = payload.get("notification_type")
        required = (
            ANY_QUIET_REQUIRED_FIELDS
            if notification_type == ANY_QUIET_SESSION
            else REQUIRED_FIELDS
        )
        for name in required:
            if name not in payload:
                raise ValidationError(f"Missing required field: {name}")

        if notification_type == ANY_QUIET_SESSION:
            return cls._any_quiet_session(payload)
        return cls._for_one_session(payload)

    @classmethod
    def _any_quiet_session(cls, payload: Dict[str, Any]) -> "NotificationRequest":
        """A rolling watch: no session identified, so a title stands in for one."""
        title = payload["title"]
        if not title or not isinstance(title, str):
            raise ValidationError("title is required and must be a string")

        side = payload["side"]
        if side not in VALID_SIDES:
            raise ValidationError("Invalid side. Must be 'left', 'right', or 'none'")

        if "minimum_slots" not in payload:
            raise ValidationError(
                "minimum_slots is required for any_quiet_session notification_type"
            )
        minimum_slots = payload["minimum_slots"]
        if not isinstance(minimum_slots, int) or minimum_slots < 0:
            raise ValidationError("minimum_slots must be a non-negative integer")

        if "time_before" not in payload:
            raise ValidationError(
                "time_before is required for any_quiet_session notification_type"
            )

        # All three optional, and each narrows the window independently: a row
        # with none of them behaves exactly as this type always has.
        days = None if payload.get("days") is None else normalize_days(payload["days"])
        not_before = (
            None
            if payload.get("not_before") is None
            else normalize_time(payload["not_before"], "not_before")
        )
        not_after = (
            None
            if payload.get("not_after") is None
            else normalize_time(payload["not_after"], "not_after")
        )
        # Canonical HH:MM is zero-padded, so lexicographic order is
        # chronological. An inverted pair is rejected rather than read as
        # wrapping past midnight: nothing here starts on one day and is still
        # starting on the next, so a wrapping window is a client bug.
        if not_before is not None and not_after is not None and not_before > not_after:
            raise ValidationError("not_before must be earlier than not_after")

        # The three columns that identify one session are NOT NULL and this
        # type has no session; empty strings are the sentinel. `clock` reads
        # them as unparseable, so nothing treats the row as a dated one.
        return cls(
            performance_ak="",
            date="",
            time="",
            side=side,
            notification_type=ANY_QUIET_SESSION,
            thresholds=None,
            minimum_slots=minimum_slots,
            time_before=normalize_duration(payload["time_before"]),
            title=title.strip(),
            days=days,
            not_before=not_before,
            not_after=not_after,
        )

    @classmethod
    def _for_one_session(cls, payload: Dict[str, Any]) -> "NotificationRequest":
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
                "Invalid notification_type. Must be 'below_threshold', "
                "'above_zero', 'quiet_session', or 'any_quiet_session'"
            )

        # Rejected rather than ignored. Every other type names one session, so a
        # day or time-of-day filter on it is either a restatement of that
        # session's own start or a way to stop a watch the user deliberately
        # created from ever firing — and the second one would be silent.
        for name in FILTER_FIELDS:
            if payload.get(name) is not None:
                raise ValidationError(
                    f"{name} is only valid for any_quiet_session notification_type"
                )

        thresholds = payload.get("thresholds")
        minimum_slots = None
        time_before = None
        if notification_type == BELOW_THRESHOLD:
            if not thresholds:
                raise ValidationError(
                    "thresholds is required for below_threshold notification_type"
                )
            if not all(isinstance(t, int) and t >= 0 for t in thresholds):
                raise ValidationError("All thresholds must be non-negative integers")
        elif notification_type == QUIET_SESSION:
            thresholds = None
            # Both are required for this type alone, so neither can join
            # REQUIRED_FIELDS without breaking the other two.
            if "minimum_slots" not in payload:
                raise ValidationError(
                    "minimum_slots is required for quiet_session notification_type"
                )
            minimum_slots = payload["minimum_slots"]
            if not isinstance(minimum_slots, int) or minimum_slots < 0:
                raise ValidationError("minimum_slots must be a non-negative integer")
            if "time_before" not in payload:
                raise ValidationError(
                    "time_before is required for quiet_session notification_type"
                )
            time_before = normalize_duration(payload["time_before"])
        else:
            thresholds = None

        return cls(
            performance_ak=performance_ak,
            date=date,
            time=time,
            side=side,
            notification_type=notification_type,
            thresholds=list(thresholds) if thresholds else None,
            minimum_slots=minimum_slots,
            time_before=time_before,
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


def _decode_json_map(raw: Any) -> Dict[str, str]:
    """Decode a JSON-object column, tolerating NULL and anything malformed.

    Unlike `_decode_json_list` there is no absent-versus-empty distinction worth
    preserving here, so an unreadable value becomes an empty map rather than
    None. Keys and values are coerced to str so a hand-edited row cannot put a
    non-string into a comparison.
    """
    if raw is None or raw == "":
        return {}
    decoded = raw
    if not isinstance(decoded, dict):
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    if not isinstance(decoded, dict):
        return {}
    return {str(key): str(value) for key, value in decoded.items()}


def _decode_days(raw: Any) -> Optional[List[str]]:
    """Decode a stored ``days`` column. None when absent, ``[]`` when unreadable.

    The read-side counterpart to `normalize_days`, tolerant in the way
    `duration_hours` is. Absent and unreadable are kept apart deliberately, and
    the empty list is free to mean the latter because `normalize_days` refuses
    to write one: a NULL means the user asked for no day filter, while a column
    holding something this cannot make sense of means the filter is *lost*.
    Folding the two together would let one corrupt row silently widen a
    weekends-only watch to every day of the week, which is the failure the
    worker skips a scan to avoid.
    """
    if raw is None or raw == "":
        return None
    decoded = raw
    if not isinstance(decoded, list):
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if not isinstance(decoded, list):
        return []
    wanted = {day.strip().lower() for day in decoded if isinstance(day, str)}
    return [day for day in VALID_DAYS if day in wanted]


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
    minimum_slots: Optional[int] = None
    time_before: Optional[str] = None
    # performance_ak -> session date, for the sessions an any_quiet_session row
    # has already fired for. The date is what lets an entry be dropped once its
    # session is over, so the map stays bounded on a row that outlives every
    # session it notifies about.
    notified_performances: Dict[str, str] = field(default_factory=dict)
    # The optional narrowing an any_quiet_session row may carry. None on each
    # axis means no filter, which is what every row predating them reads as.
    days: Optional[List[str]] = None
    not_before: Optional[str] = None
    not_after: Optional[str] = None

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
            minimum_slots=row["minimum_slots"],
            time_before=row["time_before"],
            notified_performances=_decode_json_map(row["notified_performances"]),
            days=_decode_days(row["days"]),
            not_before=row["not_before"],
            not_after=row["not_after"],
        )

    def to_api(self) -> Dict[str, Any]:
        """The wire shape clients receive.

        ``thresholds`` appears only for below_threshold notifications,
        ``minimum_slots`` and ``time_before`` only for the quiet types,
        ``days``/``not_before``/``not_after`` only for an any_quiet_session that
        set them, and ``last_checked_availability`` only once a check has run,
        matching what the app has always been sent.

        ``performance_ak``/``date``/``time`` are always present, empty for an
        any_quiet_session row: they are fields every row has rather than
        type-specific extras, so the response shape stays invariant and a
        non-nullable client-side String cannot blow up on a missing key.
        ``notified_performances`` is internal, like ``notified_thresholds``.
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
        if self.minimum_slots is not None:
            body["minimum_slots"] = self.minimum_slots
        if self.time_before is not None:
            body["time_before"] = self.time_before
        if self.days:
            body["days"] = list(self.days)
        if self.not_before is not None:
            body["not_before"] = self.not_before
        if self.not_after is not None:
            body["not_after"] = self.not_after
        if self.last_checked_availability is not None:
            body["last_checked_availability"] = self.last_checked_availability
        return body
