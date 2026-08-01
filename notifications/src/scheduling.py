"""How long to wait before re-checking a notification.

Two lookup tables, one keyed on how far away the session is and one on how
many seats are left; the tighter of the two wins. A sold-out session tomorrow
is polled every three minutes, a half-empty one in three months every four
hours. These values determine the load this service puts on the upstream API
and are carried over unchanged.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from . import clock

# Minutes between checks, indexed by whole days until the session (>15 uses [15]).
DAYS_TABLE: Sequence[int] = (3, 3, 3, 5, 5, 5, 5, 10, 10, 15, 20, 30, 60, 90, 120, 240)

# Minutes between checks, indexed by seats available (>15 uses [15]).
SEATS_TABLE: Sequence[int] = (3, 3, 3, 3, 3, 5, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240)

# Fallback when the session date/time can't be parsed, and when a performance
# has gone missing from the calendar — short, but not the every-cycle retry the
# old code did in the missing case.
FALLBACK_MINUTES = 3

# any_quiet_session opts out of both tables: it is not tracking one session's
# seat count towards a start time, it is watching a rolling window, so neither
# "days until" nor "seats left" is defined for it. A fixed cadence instead,
# scheduled onto a grid by `clock.next_slot` so every rolling row comes due in
# the same cycle and shares one calendar fetch.
ROLLING_SCAN_MINUTES = 5


def interval_minutes(
    availability: int, session_start: Optional[datetime], now: Optional[datetime] = None
) -> int:
    """Minutes to wait before the next check of this notification."""
    if session_start is None:
        return FALLBACK_MINUTES
    days = max(0, (session_start - (now or clock.now_utc())).days)
    return min(DAYS_TABLE[min(days, 15)], SEATS_TABLE[min(availability, 15)])
