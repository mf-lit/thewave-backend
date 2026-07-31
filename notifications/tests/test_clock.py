"""Session times are Europe/London; stored timestamps are UTC.

The service runs in a UTC container. The previous implementation compared
session wall-clock times against a naive local ``now()``, so through British
Summer Time every session appeared an hour younger than it was.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from src import clock


def utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def test_a_bst_session_is_past_once_it_has_started():
    """20:00 BST is 19:00 UTC — the case the old code got wrong."""
    assert clock.is_past("2026-07-30", "20:00", now=utc(2026, 7, 30, 19, 30)) is True


def test_a_bst_session_is_not_past_before_it_starts():
    assert clock.is_past("2026-07-30", "20:00", now=utc(2026, 7, 30, 18, 30)) is False


def test_a_gmt_session_is_past_once_it_has_started():
    """In winter local time is UTC, so the boundary is the stated hour."""
    assert clock.is_past("2026-01-15", "20:00", now=utc(2026, 1, 15, 20, 30)) is True
    assert clock.is_past("2026-01-15", "20:00", now=utc(2026, 1, 15, 19, 30)) is False


def test_session_start_carries_the_right_offset():
    summer = clock.session_start("2026-07-30", "20:00")
    winter = clock.session_start("2026-01-15", "20:00")
    assert summer.utcoffset().total_seconds() == 3600
    assert winter.utcoffset().total_seconds() == 0


def test_unparseable_session_times_are_never_past():
    assert clock.is_past("", "20:00") is False
    assert clock.is_past("2026-07-30", "") is False
    assert clock.is_past("30/07/2026", "20:00") is False
    assert clock.session_start("2026-07-30", "8pm") is None


def test_created_at_matches_the_format_already_in_the_database():
    """Naive UTC ISO-8601 with microseconds — the dashboard reads this column."""
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}", clock.utc_now_iso())


def test_next_check_at_stays_comparable_with_sqlite_datetime_now():
    stamp = clock.utc_stamp(utc(2026, 7, 30, 19, 42))
    assert stamp == "2026-07-30 19:42:00"


def test_utc_stamp_converts_from_other_zones():
    london_evening = clock.session_start("2026-07-30", "20:00")
    assert clock.utc_stamp(london_evening) == "2026-07-30 19:00:00"


# -- counting back from a session start ---------------------------------------

def test_hours_before_counts_back_from_a_summer_session():
    """20:00 BST is 19:00 UTC, so 24h earlier is 19:00 UTC the day before."""
    assert clock.hours_before("2026-07-30", "20:00", 24) == "2026-07-29 19:00:00"


def test_hours_before_counts_back_from_a_winter_session():
    assert clock.hours_before("2026-01-15", "20:00", 24) == "2026-01-14 20:00:00"


def test_hours_before_is_elapsed_time_across_the_spring_change():
    """The clocks go forward on 29 Mar 2026; 48h back must still be 48h.

    Subtracting on the London-local value would re-derive the offset at the
    earlier wall time and land an hour short.
    """
    assert clock.hours_before("2026-03-29", "13:00", 48) == "2026-03-27 12:00:00"


def test_hours_before_is_elapsed_time_across_the_autumn_change():
    """The clocks went back on 25 Oct 2026, so the error is the other way.

    13:00 GMT on the 26th is 13:00 UTC; 48h earlier is 13:00 UTC, which is
    14:00 local because the 24th is still BST. Wall-clock arithmetic would
    answer 12:00 UTC.
    """
    assert clock.hours_before("2026-10-26", "13:00", 48) == "2026-10-24 13:00:00"


def test_hours_before_is_none_when_it_cannot_be_computed():
    assert clock.hours_before("30/07/2026", "20:00", 24) is None
    assert clock.hours_before("2026-07-30", "20:00", None) is None


def test_just_after_start_lands_past_the_session():
    """Parked a minute late, so `due()`'s <= cannot beat `is_past`'s <."""
    assert clock.just_after_start("2026-07-30", "20:00") == "2026-07-30 19:01:00"
    assert clock.is_past("2026-07-30", "20:00", now=utc(2026, 7, 30, 19, 1)) is True


def test_just_after_start_is_none_for_an_unparseable_session():
    assert clock.just_after_start("2026-07-30", "8pm") is None
