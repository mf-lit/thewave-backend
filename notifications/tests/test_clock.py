"""Session times are Europe/London; stored timestamps are UTC.

The service runs in a UTC container. The previous implementation compared
session wall-clock times against a naive local ``now()``, so through British
Summer Time every session appeared an hour younger than it was.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

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


# -- rolling windows ----------------------------------------------------------

NOW = utc(2026, 7, 31, 10, 7)  # 11:07 London, BST


@pytest.mark.parametrize(
    "date, time, expected",
    [
        ("2026-08-01", "10:00", True),    # +23h
        ("2026-07-31", "11:08", True),    # a minute away
        ("2026-08-01", "12:00", False),   # +25h, past the window
        ("2026-07-31", "09:00", False),   # already started
    ],
)
def test_within_hours_bounds_the_window_at_both_ends(date, time, expected):
    assert clock.within_hours(date, time, 24, now=NOW) is expected


def test_within_hours_includes_a_session_exactly_on_the_far_edge():
    assert clock.within_hours("2026-08-01", "11:07", 24, now=NOW) is True


def test_the_near_edge_agrees_with_is_past():
    """A session starting exactly now is in the window and not yet past.

    `within_hours` uses <= at the near edge and `is_past` uses <, so there is
    neither a moment where both are true nor a gap where neither is.
    """
    assert clock.within_hours("2026-07-31", "11:07", 24, now=NOW) is True
    assert clock.is_past("2026-07-31", "11:07", now=NOW) is False

    a_second_later = utc(2026, 7, 31, 10, 7).replace(second=1)
    assert clock.within_hours("2026-07-31", "11:07", 24, now=a_second_later) is False
    assert clock.is_past("2026-07-31", "11:07", now=a_second_later) is True


@pytest.mark.parametrize("date, time, hours", [("", "", 24), ("2026-08-01", "8pm", 24),
                                               ("2026-08-01", "10:00", None)])
def test_within_hours_is_false_for_anything_it_cannot_read(date, time, hours):
    assert clock.within_hours(date, time, hours, now=NOW) is False


def test_dates_within_covers_the_days_the_window_touches():
    assert clock.dates_within(24, now=NOW) == ["2026-07-31", "2026-08-01"]
    assert clock.dates_within(48, now=NOW) == ["2026-07-31", "2026-08-01", "2026-08-02"]


def test_dates_within_never_exceeds_three_days_at_the_maximum():
    assert len(clock.dates_within(48, now=utc(2026, 7, 31, 23, 30))) <= 3


@pytest.mark.parametrize("now", [utc(2026, 10, 24, 20, 0), utc(2026, 3, 28, 20, 0)])
def test_dates_within_spans_a_clock_change(now):
    """October adds an hour and March loses one; neither may drop a day."""
    dates = clock.dates_within(48, now=now)
    assert len(dates) == 3
    assert dates == sorted(dates)


def test_next_slot_lands_on_the_grid_not_a_fixed_offset():
    assert clock.next_slot(5, now=utc(2026, 7, 31, 10, 7)) == "2026-07-31 10:10:00"
    assert clock.next_slot(5, now=utc(2026, 7, 31, 10, 6)) == "2026-07-31 10:10:00"


def test_next_slot_is_always_in_the_future():
    """On a boundary it moves to the next one, so a scan cannot busy-loop."""
    assert clock.next_slot(5, now=utc(2026, 7, 31, 10, 5)) == "2026-07-31 10:10:00"


def test_rows_woken_minutes_apart_converge_on_one_slot():
    """This is what lets every rolling row share a single calendar fetch."""
    slots = {clock.next_slot(5, now=utc(2026, 7, 31, 10, m)) for m in (6, 7, 8, 9)}
    assert slots == {"2026-07-31 10:10:00"}


# -- day and time-of-day filters ----------------------------------------------

@pytest.mark.parametrize(
    "date, expected",
    [
        ("2026-07-27", "mon"),
        ("2026-07-31", "fri"),
        ("2026-08-01", "sat"),
        ("2026-08-02", "sun"),
    ],
)
def test_day_name_reads_the_local_calendar_day(date, expected):
    assert clock.day_name(date) == expected


@pytest.mark.parametrize("date", ["", None, "2026-08-32", "Saturday"])
def test_day_name_is_none_for_anything_it_cannot_read(date):
    assert clock.day_name(date) is None


def test_no_filters_matches_everything():
    """A row with none of them set behaves exactly as the type always has."""
    assert clock.matches_schedule("2026-08-01", "10:00") is True


@pytest.mark.parametrize("expected, days", [(True, ["sat", "sun"]), (False, ["mon", "tue"])])
def test_the_day_filter_admits_only_its_own_days(expected, days):
    assert clock.matches_schedule("2026-08-01", "10:00", days=days) is expected


def test_a_bst_session_just_after_midnight_keeps_its_local_day():
    """00:30 BST on Sunday is 23:30 UTC on Saturday — the whole point of `day_name`."""
    assert clock.matches_schedule("2026-08-02", "00:30", days=["sun"]) is True
    assert clock.matches_schedule("2026-08-02", "00:30", days=["sat"]) is False


@pytest.mark.parametrize("date", ["2026-03-29", "2026-10-25"])
def test_a_session_on_a_clock_change_keeps_its_local_day(date):
    """Spring forward and autumn back both land on a Sunday."""
    assert clock.matches_schedule(date, "10:00", days=["sun"]) is True


@pytest.mark.parametrize(
    "time, expected",
    [("08:59", False), ("09:00", True), ("11:00", True), ("13:00", True), ("13:01", False)],
)
def test_both_time_bounds_are_inclusive(time, expected):
    matches = clock.matches_schedule(
        "2026-08-01", time, not_before="09:00", not_after="13:00"
    )
    assert matches is expected


def test_either_bound_may_stand_alone():
    assert clock.matches_schedule("2026-08-01", "20:00", not_before="09:00") is True
    assert clock.matches_schedule("2026-08-01", "20:00", not_after="13:00") is False


def test_the_filters_and_together():
    """The right time on the wrong day is still a miss."""
    assert clock.matches_schedule(
        "2026-08-01", "10:00", days=["sat"], not_before="09:00", not_after="13:00"
    ) is True
    assert clock.matches_schedule(
        "2026-07-31", "10:00", days=["sat"], not_before="09:00", not_after="13:00"
    ) is False


def test_an_hour_before_noon_is_not_read_as_a_bigger_number():
    """String comparison is only correct because both sides are zero-padded."""
    assert clock.matches_schedule("2026-08-01", "09:00", not_before="10:00") is False


@pytest.mark.parametrize("date, time", [("", ""), ("2026-08-01", "8pm"), ("nope", "10:00")])
def test_matches_schedule_is_false_for_anything_it_cannot_read(date, time):
    """The empty date and time on an any_quiet_session row can never look like a match."""
    assert clock.matches_schedule(date, time, days=["sat"]) is False
    assert clock.matches_schedule(date, time) is False
