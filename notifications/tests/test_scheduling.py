"""Check-interval tables.

These govern how hard this service polls the upstream API, so the values are
pinned rather than recomputed.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from src import clock
from src.scheduling import DAYS_TABLE, FALLBACK_MINUTES, SEATS_TABLE, interval_minutes

NOW = clock.now_utc()


def session_in(days: int = 0, hours: int = 0):
    return NOW + timedelta(days=days, hours=hours)


def test_falls_back_when_the_session_time_is_unknown():
    assert interval_minutes(5, None, NOW) == FALLBACK_MINUTES


def test_the_tighter_of_the_two_tables_wins():
    # 30 days out (240 min) but only one seat left (3 min).
    assert interval_minutes(1, session_in(days=30), NOW) == 3
    # Tomorrow (3 min) with plenty of seats (240 min).
    assert interval_minutes(20, session_in(days=1), NOW) == 3


def test_imminent_and_sold_out_polls_fastest():
    assert interval_minutes(0, session_in(hours=2), NOW) == 3


def test_distant_and_empty_polls_slowest():
    assert interval_minutes(30, session_in(days=40), NOW) == 240


@pytest.mark.parametrize("days", range(16))
def test_days_table_is_applied_verbatim(days):
    assert interval_minutes(99, session_in(days=days), NOW) == DAYS_TABLE[days]


@pytest.mark.parametrize("seats", range(16))
def test_seats_table_is_applied_verbatim(seats):
    assert interval_minutes(seats, session_in(days=99), NOW) == SEATS_TABLE[seats]


def test_tables_are_clamped_beyond_their_last_entry():
    assert interval_minutes(500, session_in(days=500), NOW) == min(
        DAYS_TABLE[15], SEATS_TABLE[15]
    )


def test_a_session_already_underway_uses_the_zero_day_entry():
    assert interval_minutes(10, session_in(hours=-5), NOW) == min(DAYS_TABLE[0], SEATS_TABLE[10])
