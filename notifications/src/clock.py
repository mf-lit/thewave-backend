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

from datetime import datetime, timedelta, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

LONDON = ZoneInfo("Europe/London")

SESSION_FORMAT = "%Y-%m-%d %H:%M"
DATE_FORMAT = "%Y-%m-%d"
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

# How far past a session's start a retired notification is parked. `due()`
# selects on ``next_check_at <= now`` while `is_past` tests ``start < now``, so
# parking on the start itself leaves a one-second window in which a row is due
# but not yet past, and would be processed once more.
RETIRE_MINUTES = 1


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


def hours_before(date_str: str, time_str: str, hours: Optional[int]) -> Optional[str]:
    """UTC stamp ``hours`` real hours before a session starts.

    The subtraction happens after converting to UTC, deliberately. Doing it on
    the London-local value is wall-clock arithmetic: zoneinfo re-derives the
    offset at the resulting wall time, so a 48h window spanning a DST change
    would come out an hour short or long.

    Returns None when the session or the duration can't be read, so callers can
    fall back the same way `scheduling.interval_minutes` already does.
    """
    start = session_start(date_str, time_str)
    if start is None or hours is None:
        return None
    return utc_stamp(start.astimezone(timezone.utc) - timedelta(hours=hours))


def within_hours(
    date_str: str, time_str: str, hours: Optional[int], now: Optional[datetime] = None
) -> bool:
    """Whether a session starts inside the window [now, now + hours].

    ``start >= now`` also excludes a session that has already begun, so this is
    the whole membership test for a rolling watch — there is no separate
    `is_past` check on that path. Unparseable input is not in any window.
    """
    start = session_start(date_str, time_str)
    if start is None or hours is None:
        return False
    moment = now or now_utc()
    return moment <= start <= moment + timedelta(hours=hours)


def dates_within(hours: int, now: Optional[datetime] = None) -> List[str]:
    """Every London-local calendar date the window from now to now+hours touches.

    At most three at the 48h maximum. The timedelta is added to an aware
    datetime and the local date read off the result, so a window spanning a
    clock change still covers the right days.
    """
    start = (now or now_utc()).astimezone(LONDON)
    end = start + timedelta(hours=hours)
    dates, day = [], start.date()
    while day <= end.date():
        dates.append(day.strftime(DATE_FORMAT))
        day += timedelta(days=1)
    return dates


def next_slot(minutes: int, now: Optional[datetime] = None) -> str:
    """UTC stamp of the next boundary on a fixed ``minutes`` grid.

    Rolling watches share one calendar fetch only if they come due together, so
    they are scheduled onto a grid rather than ``now + interval`` — otherwise
    rows created minutes apart wake separately and each pays its own fetch.
    """
    moment = (now or now_utc()).astimezone(timezone.utc).replace(second=0, microsecond=0)
    return utc_stamp(moment + timedelta(minutes=minutes - (moment.minute % minutes)))


def just_after_start(date_str: str, time_str: str) -> Optional[str]:
    """UTC stamp a minute past session start — where a retired row is parked."""
    start = session_start(date_str, time_str)
    if start is None:
        return None
    return utc_stamp(start.astimezone(timezone.utc) + timedelta(minutes=RETIRE_MINUTES))


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
