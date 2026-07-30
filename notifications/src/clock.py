"""Time handling.

Two distinct notions of time live in this service and used to be conflated:

* **Session times** (``date`` + ``time`` on a notification) are The Wave's
  local wall-clock, i.e. Europe/London. They are what a user reads off the
  booking page.
* **Stored timestamps** (``created_at``, ``updated_at``, ``next_check_at``)
  are UTC, written in the exact naive formats already present in the
  database so existing rows and the dashboard keep working.

The old code compared session times against a naive ``datetime.now()`` inside
a UTC container, so during BST every session looked an hour younger than it
was and past sessions lingered before being deleted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")

SESSION_FORMAT = "%Y-%m-%d %H:%M"
DATE_FORMAT = "%Y-%m-%d"
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_utc() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def session_start(date_str: str, time_str: str) -> Optional[datetime]:
    """Parse a session's local date/time into an aware datetime.

    Returns None when either field is missing or malformed.
    """
    if not date_str or not time_str:
        return None
    try:
        naive = datetime.strptime(f"{date_str} {time_str}", SESSION_FORMAT)
    except ValueError:
        return None
    return naive.replace(tzinfo=LONDON)


def is_past(date_str: str, time_str: str, now: Optional[datetime] = None) -> bool:
    """Whether a session has already started. Unparseable input is not past."""
    start = session_start(date_str, time_str)
    if start is None:
        return False
    return start < (now or now_utc())


def utc_now_iso() -> str:
    """Timestamp for ``created_at`` / ``updated_at``.

    Naive UTC ISO-8601 with microseconds — byte-compatible with the rows
    written by the previous implementation's ``datetime.utcnow().isoformat()``.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def utc_stamp(moment: datetime) -> str:
    """Timestamp for ``next_check_at``.

    ``%Y-%m-%d %H:%M:%S`` in UTC, so it stays directly comparable with
    SQLite's ``datetime('now')``.
    """
    return moment.astimezone(timezone.utc).strftime(STAMP_FORMAT)
