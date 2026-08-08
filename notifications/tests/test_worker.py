"""The check cycle."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dataclasses import replace

from src import clock, scheduling
from src.calendar_client import CalendarError
from src.models import NotificationRequest
from src.push import PushError
from src.worker import Worker
from tests.conftest import VALID_TOKEN, make_day, make_performance, soon

FUTURE = "2027-08-05"
PAST = "2020-01-01"
AK = "TWB.EVN6.PRF8962"


@pytest.fixture
def worker(services) -> Worker:
    return Worker(services)


@pytest.fixture
def stocked_calendar(calendar):
    def stock(seats, date=FUTURE, performance_ak=AK):
        calendar.days = [
            make_day(
                date,
                [make_performance(performance_ak=performance_ak, availability=seats)],
            )
        ]
        return calendar

    return stock


def add(services, client_id="client-1", date=FUTURE, **overrides):
    payload = {
        "performance_ak": AK,
        "date": date,
        "time": "18:00",
        "side": "right",
        "notification_type": "above_zero",
    }
    payload.update(overrides)
    return services.notifications.create(
        client_id, NotificationRequest.from_payload(payload), "Advanced Surf"
    )


def stored(services, notification):
    return services.notifications.get(notification.client_id, notification.notification_id)


# -- the basic cycle ----------------------------------------------------------

def test_records_availability_and_schedules_the_next_check(
    worker, services, stocked_calendar
):
    stocked_calendar({"right": 7})
    notification = add(services)

    worker.run_once()

    updated = stored(services, notification)
    assert updated.last_checked_availability == 7
    assert updated.next_check_at is not None


def test_only_due_notifications_are_checked(worker, services, stocked_calendar, calendar):
    stocked_calendar({"right": 7})
    add(services)

    worker.run_once()
    assert len(calendar.requested_dates) == 1

    # Everything is now scheduled into the future, so a second pass is a no-op.
    worker.run_once()
    assert len(calendar.requested_dates) == 1


def test_only_the_dates_in_play_are_fetched(worker, services, calendar):
    calendar.days = [
        make_day(FUTURE, [make_performance(performance_ak=AK, availability={"right": 4})]),
        make_day("2027-08-09", [make_performance(performance_ak="OTHER")]),
    ]
    add(services)

    worker.run_once()
    assert calendar.requested_dates == [[FUTURE]]


def test_a_cycle_with_nothing_due_makes_no_upstream_call(worker, calendar):
    worker.run_once()
    assert calendar.requested_dates == []


# -- deletion of past sessions ------------------------------------------------

def test_a_started_session_is_deleted(worker, services, stocked_calendar):
    stocked_calendar({"right": 4}, date=PAST)
    notification = add(services, date=PAST)

    worker.run_once()
    assert stored(services, notification) is None


def test_a_past_session_is_deleted_even_without_calendar_data(worker, services, calendar):
    calendar.days = []
    notification = add(services, date=PAST)

    worker.run_once()
    assert stored(services, notification) is None


# -- pushes -------------------------------------------------------------------

def test_a_crossed_threshold_pushes_once(worker, services, sender, stocked_calendar):
    stocked_calendar({"right": 2})
    services.clients.upsert_token("client-1", VALID_TOKEN)
    notification = add(
        services, notification_type="below_threshold", thresholds=[5, 2]
    )

    worker.run_once()

    assert len(sender.sent) == 1
    assert sender.sent[0].data["threshold"] == "5"
    assert stored(services, notification).notified_thresholds == [5, 2]


def test_a_notified_threshold_does_not_fire_again(worker, services, sender, stocked_calendar):
    calendar = stocked_calendar({"right": 2})
    services.clients.upsert_token("client-1", VALID_TOKEN)
    notification = add(services, notification_type="below_threshold", thresholds=[5])

    worker.run_once()
    # Make it due again with the seat count unchanged.
    services.notifications.reschedule(notification, "2000-01-01 00:00:00")
    worker.run_once()

    assert len(sender.sent) == 1
    assert calendar.requested_dates == [[FUTURE], [FUTURE]]


def test_above_zero_fires_on_the_transition_and_then_stays_quiet(
    worker, services, sender, stocked_calendar
):
    calendar = stocked_calendar({"right": 0})
    services.clients.upsert_token("client-1", VALID_TOKEN)
    notification = add(services)

    worker.run_once()  # sold out: records 0, no push
    assert sender.sent == []

    calendar.days = [make_day(FUTURE, [make_performance(performance_ak=AK, availability={"right": 3})])]
    services.notifications.reschedule(notification, "2000-01-01 00:00:00")
    worker.run_once()  # a seat appears: push
    assert len(sender.sent) == 1

    services.notifications.reschedule(notification, "2000-01-01 00:00:00")
    worker.run_once()  # still available: silent
    assert len(sender.sent) == 1


def test_the_reading_is_recorded_even_when_the_push_fails(
    worker, services, sender, stocked_calendar
):
    stocked_calendar({"right": 3})
    services.clients.upsert_token("client-1", VALID_TOKEN)
    sender.error = PushError("firebase is unavailable")
    notification = add(services)

    worker.run_once()
    assert stored(services, notification).last_checked_availability == 3


def test_no_token_means_no_push_but_still_a_reading(
    worker, services, sender, stocked_calendar
):
    stocked_calendar({"right": 3})
    notification = add(services)

    worker.run_once()
    assert sender.sent == []
    assert stored(services, notification).last_checked_availability == 3


# -- degraded upstream --------------------------------------------------------

def test_a_calendar_outage_leaves_state_untouched(worker, services, calendar):
    calendar.error = CalendarError("connection refused")
    notification = add(services)

    worker.run_once()

    unchanged = stored(services, notification)
    assert unchanged.last_checked_availability is None
    assert unchanged.next_check_at is None


def test_a_vanished_performance_is_rescheduled_rather_than_retried_every_cycle(
    worker, services, calendar
):
    """The old code left next_check_at unset here, re-fetching it forever."""
    calendar.days = [make_day(FUTURE, [])]
    notification = add(services)

    worker.run_once()

    updated = stored(services, notification)
    assert updated.next_check_at is not None
    assert updated.last_checked_availability is None


def test_a_side_that_stops_being_sold_is_rescheduled(worker, services, stocked_calendar):
    stocked_calendar({"left": 4})  # notification watches "right"
    notification = add(services)

    worker.run_once()
    assert stored(services, notification).next_check_at is not None


def test_one_broken_notification_does_not_stall_the_others(
    worker, services, stocked_calendar, monkeypatch
):
    stocked_calendar({"right": 4})
    first = add(services, client_id="client-1")
    second = add(services, client_id="client-2")

    original = Worker.process
    calls = {"n": 0}

    def flaky(self, notification, calendar):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return original(self, notification, calendar)

    monkeypatch.setattr(Worker, "process", flaky)
    worker.run_once()

    checked = [stored(services, n).last_checked_availability for n in (first, second)]
    assert checked.count(4) == 1


# -- loop plumbing ------------------------------------------------------------

def test_sleep_is_bounded(worker, services, stocked_calendar):
    stocked_calendar({"right": 15})
    add(services)

    assert worker.seconds_until_next_cycle() == 30  # nothing scheduled yet
    worker.run_once()
    assert 30 <= worker.seconds_until_next_cycle() <= 60


def test_the_heartbeat_is_written(worker, services):
    worker.heartbeat()
    assert services.settings.heartbeat_path.read_text()


def test_run_forever_stops_when_the_event_is_set(worker):
    import threading

    stop = threading.Event()
    stop.set()
    worker.run_forever(stop)  # returns immediately rather than sleeping


# -- quiet_session ------------------------------------------------------------

def add_quiet(services, date=FUTURE, **overrides):
    fields = {
        "notification_type": "quiet_session",
        "minimum_slots": 12,
        "time_before": "24h",
    }
    fields.update(overrides)
    return add(services, date=date, **fields)


def test_a_quiet_session_is_not_checked_before_its_window_opens(
    worker, services, sender, stocked_calendar, calendar
):
    stocked_calendar({"right": 20})
    services.clients.upsert_token("client-1", VALID_TOKEN)
    add_quiet(services)

    worker.run_once()

    assert calendar.requested_dates == []
    assert sender.sent == []


def test_a_quiet_session_pushes_once_its_check_comes_due(
    worker, services, sender, stocked_calendar
):
    stocked_calendar({"right": 20})
    services.clients.upsert_token("client-1", VALID_TOKEN)
    notification = add_quiet(services)
    services.notifications.reschedule(notification, "2000-01-01 00:00:00")

    worker.run_once()

    assert len(sender.sent) == 1
    assert sender.sent[0].body == "Possible quiet session: 20 slots remaining on the right"
    assert sender.sent[0].data["minimum_slots"] == "12"
    assert sender.sent[0].data["threshold"] == ""


def test_a_busy_session_is_checked_but_not_pushed(
    worker, services, sender, stocked_calendar
):
    stocked_calendar({"right": 4})
    services.clients.upsert_token("client-1", VALID_TOKEN)
    notification = add_quiet(services)
    services.notifications.reschedule(notification, "2000-01-01 00:00:00")

    worker.run_once()

    assert sender.sent == []
    assert stored(services, notification).last_checked_availability == 4


def test_a_quiet_session_is_retired_after_its_single_check(
    worker, services, stocked_calendar
):
    """Parked past the session start, where the deletion branch collects it."""
    stocked_calendar({"right": 20})
    notification = add_quiet(services)
    services.notifications.reschedule(notification, "2000-01-01 00:00:00")

    worker.run_once()

    # 18:00 on 2027-08-05 is BST, i.e. 17:00 UTC, plus the retire minute.
    assert stored(services, notification).next_check_at == "2027-08-05 17:01:00"


def test_a_quiet_session_never_pushes_twice(worker, services, sender, stocked_calendar):
    """Even forced back to due repeatedly, the reading is what stops it."""
    stocked_calendar({"right": 20})
    services.clients.upsert_token("client-1", VALID_TOKEN)
    notification = add_quiet(services)

    for _ in range(3):
        services.notifications.reschedule(notification, "2000-01-01 00:00:00")
        worker.run_once()

    assert len(sender.sent) == 1


def test_a_retired_quiet_session_is_deleted_once_the_session_starts(
    worker, services, stocked_calendar
):
    stocked_calendar({"right": 20}, date=PAST)
    notification = add_quiet(services, date=PAST)
    services.notifications.reschedule(notification, "2000-01-01 00:00:00")

    worker.run_once()
    assert stored(services, notification) is None


def freeze_after_check_time(monkeypatch, minutes: int) -> None:
    """Pin the clock ``minutes`` past a FUTURE quiet_session's intended check."""
    fire_at = clock.hours_before(FUTURE, "18:00", 24)
    moment = datetime.strptime(fire_at, clock.STAMP_FORMAT).replace(
        tzinfo=timezone.utc
    ) + timedelta(minutes=minutes)
    monkeypatch.setattr(clock, "now_utc", lambda: moment)


def test_a_quiet_session_still_retries_inside_its_grace_window(
    worker, services, calendar, monkeypatch
):
    """A transient calendar gap right at the check time is worth retrying."""
    calendar.days = [make_day(FUTURE, [make_performance(performance_ak="OTHER")])]
    notification = add_quiet(services)
    services.notifications.reschedule(notification, "2000-01-01 00:00:00")
    freeze_after_check_time(monkeypatch, 5)

    worker.run_once()

    assert stored(services, notification).next_check_at == "2027-08-04 17:08:00"


def test_a_quiet_session_gives_up_on_a_performance_that_stays_missing(
    worker, services, sender, calendar, monkeypatch
):
    """Its one check is only meaningful near the moment it was scheduled for.

    Left retrying, a 'starting soon, and quiet' push could arrive hours late
    against a seat count that no longer means what the user asked about.
    """
    calendar.days = [make_day(FUTURE, [make_performance(performance_ak="OTHER")])]
    services.clients.upsert_token("client-1", VALID_TOKEN)
    notification = add_quiet(services)
    services.notifications.reschedule(notification, "2000-01-01 00:00:00")
    freeze_after_check_time(monkeypatch, 31)

    worker.run_once()

    assert sender.sent == []
    assert stored(services, notification).next_check_at == "2027-08-05 17:01:00"


def test_a_quiet_session_gives_up_when_its_side_stops_being_sold(
    worker, services, calendar, monkeypatch
):
    calendar.days = [
        make_day(FUTURE, [make_performance(performance_ak=AK, availability={"left": 9})])
    ]
    notification = add_quiet(services)
    services.notifications.reschedule(notification, "2000-01-01 00:00:00")
    freeze_after_check_time(monkeypatch, 31)

    worker.run_once()

    assert stored(services, notification).next_check_at == "2027-08-05 17:01:00"


def test_a_far_future_quiet_session_does_not_change_the_sleep_bounds(worker, services):
    add_quiet(services)
    assert 30 <= worker.seconds_until_next_cycle() <= 60


# -- any_quiet_session --------------------------------------------------------

def add_rolling(services, client_id="client-1", **overrides):
    fields = {
        "notification_type": "any_quiet_session",
        "title": "Advanced Surf",
        "side": "right",
        "minimum_slots": 8,
        "time_before": "24h",
    }
    fields.update(overrides)
    return services.notifications.create(
        client_id, NotificationRequest.from_payload(fields), fields["title"]
    )


def session_in(hours, performance_ak="P1", title="Advanced Surf", seats=None):
    """A day holding one performance that many hours from now."""
    date, time = soon(hours)
    return make_day(
        date,
        [make_performance(performance_ak, title, time, seats or {"left": 0, "right": 9})],
    )


def make_due(services, notification):
    """Bring a rolling row forward so the next cycle picks it up again."""
    services.notifications.reschedule(notification, "2020-01-01 00:00:00")
    return services.notifications.get(notification.client_id, notification.notification_id)


@pytest.fixture
def rolling_ready(services, calendar):
    services.clients.upsert_token("client-1", VALID_TOKEN)
    return calendar


def test_a_rolling_watch_pushes_for_a_quiet_session_in_its_window(
    worker, services, sender, rolling_ready
):
    rolling_ready.days = [session_in(2)]
    add_rolling(services)

    worker.run_once()

    assert len(sender.sent) == 1
    assert sender.sent[0].data["performance_ak"] == "P1"
    assert sender.sent[0].data["availability"] == "9"
    assert sender.sent[0].body == "Possible quiet session: 9 slots remaining on the right"


def test_the_push_describes_the_matched_session_not_the_row(
    worker, services, sender, rolling_ready
):
    date, time = soon(2)
    rolling_ready.days = [session_in(2)]
    notification = add_rolling(services)

    worker.run_once()

    data = sender.sent[0].data
    assert (data["date"], data["time"]) == (date, time)
    assert data["session_title"] == "Advanced Surf"
    assert data["notification_type"] == "any_quiet_session"
    assert data["minimum_slots"] == "8"
    assert data["threshold"] == ""

    # The row itself still stores no session.
    row = stored(services, notification)
    assert (row.performance_ak, row.date, row.time) == ("", "", "")


def test_a_busy_session_is_not_pushed_for(worker, services, sender, rolling_ready):
    rolling_ready.days = [session_in(2, seats={"right": 7})]
    notification = add_rolling(services)

    worker.run_once()

    assert sender.sent == []
    assert stored(services, notification).notified_performances == {}


def test_a_rolling_watch_never_records_a_reading(worker, services, rolling_ready):
    """last_checked_availability is a per-session field on a row without one."""
    rolling_ready.days = [session_in(2)]
    notification = add_rolling(services)

    worker.run_once()

    assert stored(services, notification).last_checked_availability is None


def test_one_session_is_never_pushed_for_twice(worker, services, sender, rolling_ready):
    """The requirement: quiet, then busy, then quiet again still fires once."""
    rolling_ready.days = [session_in(2)]
    notification = add_rolling(services)

    worker.run_once()
    assert len(sender.sent) == 1

    for seats in ({"right": 0}, {"right": 20}, {"right": 9}):
        rolling_ready.days = [session_in(2, seats=seats)]
        make_due(services, notification)
        worker.run_once()

    assert len(sender.sent) == 1


def test_a_second_session_going_quiet_later_still_pushes(
    worker, services, sender, rolling_ready
):
    rolling_ready.days = [session_in(2, "P1")]
    notification = add_rolling(services)
    worker.run_once()
    assert [m.data["performance_ak"] for m in sender.sent] == ["P1"]

    date, time = soon(3)
    rolling_ready.days = [
        make_day(
            date,
            [
                make_performance("P1", "Advanced Surf", time, {"right": 9}),
                make_performance("P2", "Advanced Surf", time, {"right": 12}),
            ],
        )
    ]
    make_due(services, notification)
    worker.run_once()

    assert [m.data["performance_ak"] for m in sender.sent] == ["P1", "P2"]


def test_two_matches_in_one_cycle_both_push_soonest_first(
    worker, services, sender, rolling_ready
):
    early_date, early_time = soon(2)
    late_date, late_time = soon(3)
    rolling_ready.days = [
        make_day(late_date, [make_performance("LATE", "Advanced Surf", late_time, {"right": 9})]),
        make_day(early_date, [make_performance("EARLY", "Advanced Surf", early_time, {"right": 9})]),
    ] if early_date != late_date else [
        make_day(
            early_date,
            [
                make_performance("LATE", "Advanced Surf", late_time, {"right": 9}),
                make_performance("EARLY", "Advanced Surf", early_time, {"right": 9}),
            ],
        )
    ]
    notification = add_rolling(services)

    worker.run_once()

    assert [m.data["performance_ak"] for m in sender.sent] == ["EARLY", "LATE"]
    assert set(stored(services, notification).notified_performances) == {"EARLY", "LATE"}


def test_a_session_beyond_the_window_is_not_pushed_for(
    worker, services, sender, rolling_ready
):
    rolling_ready.days = [session_in(30)]
    add_rolling(services)  # 24h window

    worker.run_once()

    assert sender.sent == []


def test_a_session_that_has_already_started_is_not_pushed_for(
    worker, services, sender, rolling_ready
):
    rolling_ready.days = [session_in(-1)]
    add_rolling(services)

    worker.run_once()

    assert sender.sent == []


def test_a_wider_window_reaches_a_later_session(worker, services, sender, rolling_ready):
    rolling_ready.days = [session_in(30)]
    add_rolling(services, time_before="48h")

    worker.run_once()

    assert len(sender.sent) == 1


@pytest.mark.parametrize(
    "day",
    [
        session_in(2, title="Advanced Surf Lesson"),
        session_in(2, title="Advanced Coaching (In Water)"),
        session_in(2, seats={"left": 9}),
    ],
)
def test_a_near_miss_is_ignored(worker, services, sender, rolling_ready, day):
    rolling_ready.days = [day]
    add_rolling(services)

    worker.run_once()

    assert sender.sent == []


def test_a_rolling_watch_is_never_deleted(worker, services, rolling_ready):
    """It has no session to expire against; only the client removes it."""
    rolling_ready.days = [session_in(2)]
    notification = add_rolling(services)

    for _ in range(3):
        make_due(services, notification)
        worker.run_once()

    assert stored(services, notification) is not None


def test_a_rolling_watch_reschedules_onto_the_grid(worker, services, rolling_ready):
    rolling_ready.days = [session_in(2)]
    notification = add_rolling(services)

    worker.run_once()

    next_check = stored(services, notification).next_check_at
    assert next_check == clock.next_slot(scheduling.ROLLING_SCAN_MINUTES)
    assert int(next_check[14:16]) % scheduling.ROLLING_SCAN_MINUTES == 0


def test_rolling_watches_share_one_slot_and_so_one_fetch(worker, services, rolling_ready):
    rolling_ready.days = [session_in(2)]
    first = add_rolling(services)
    second = add_rolling(services, client_id="client-2")

    worker.run_once()

    assert stored(services, first).next_check_at == stored(services, second).next_check_at
    assert len(rolling_ready.requested_dates) == 1


def test_an_empty_calendar_leaves_the_watch_scheduled(
    worker, services, sender, rolling_ready
):
    rolling_ready.days = []
    notification = add_rolling(services)

    worker.run_once()

    assert sender.sent == []
    assert stored(services, notification).next_check_at is not None


def test_a_corrupt_time_before_reschedules_rather_than_crashing(
    worker, services, sender, rolling_ready
):
    rolling_ready.days = [session_in(2)]
    notification = add_rolling(services)
    services.notifications.db.connection().execute(
        "UPDATE notifications SET time_before = 'soon' WHERE notification_id = ?",
        (notification.notification_id,),
    )
    services.notifications.db.connection().commit()

    worker.run_once()

    assert sender.sent == []
    assert stored(services, notification).next_check_at is not None


def test_a_corrupt_time_before_contributes_no_dates(services):
    notification = add_rolling(services)
    broken = replace(notification, time_before="soon")
    assert Worker._scan_dates(broken) == []


def test_the_window_is_clamped_to_the_maximum(services):
    """A hand-edited row must not ask the calendar for an arbitrary range."""
    notification = replace(add_rolling(services), time_before="1000h")
    assert len(Worker._scan_dates(notification)) == 3


def test_run_once_fetches_the_union_of_fixed_dates_and_rolling_windows(
    worker, services, rolling_ready
):
    add(services, date=FUTURE)
    add_rolling(services)

    worker.run_once()

    requested = rolling_ready.requested_dates[0]
    assert requested == sorted(requested)
    assert FUTURE in requested
    assert set(clock.dates_within(24)) <= set(requested)


def test_a_notified_session_is_forgotten_once_its_date_has_passed(
    worker, services, sender, rolling_ready
):
    """Otherwise the map grows without bound on a row that outlives its sessions."""
    rolling_ready.days = [session_in(2, "P1")]
    notification = add_rolling(services)
    worker.run_once()

    services.notifications.record_notified_performances(
        notification, {"P1": "2020-01-01", "P2": "2020-06-01"}
    )
    date, time = soon(3)
    rolling_ready.days = [
        make_day(date, [make_performance("P3", "Advanced Surf", time, {"right": 9})])
    ]
    make_due(services, notification)
    worker.run_once()

    assert set(stored(services, notification).notified_performances) == {"P3"}


# -- day and time-of-day filters ----------------------------------------------

# A Saturday. 08:00 UTC is 09:00 BST, so a session at 11:00 London is two hours
# ahead and comfortably inside a 24h window that also reaches into Sunday.
SATURDAY = datetime(2026, 8, 1, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def saturday(monkeypatch, calendar, services):
    """Pin the clock, so a session's weekday and start time are not the test's guess."""
    monkeypatch.setattr(clock, "now_utc", lambda: SATURDAY)
    services.clients.upsert_token("client-1", VALID_TOKEN)
    calendar.days = [
        make_day("2026-08-01", [make_performance("P1", "Advanced Surf", "11:00", {"right": 9})])
    ]
    return calendar


def test_a_session_on_an_allowed_day_still_pushes(worker, services, sender, saturday):
    add_rolling(services, days=["sat", "sun"])

    worker.run_once()

    assert [m.data["performance_ak"] for m in sender.sent] == ["P1"]


def test_a_session_on_an_excluded_day_is_not_pushed_for(
    worker, services, sender, saturday
):
    notification = add_rolling(services, days=["mon", "tue"])

    worker.run_once()

    assert sender.sent == []
    # Not remembered either: the session was never a candidate, so a later
    # widening of the filter must still be able to fire for it.
    assert stored(services, notification).notified_performances == {}


def test_a_session_exactly_on_both_bounds_still_pushes(worker, services, sender, saturday):
    """Both ends are inclusive: 'nothing before 11:00' includes an 11:00 session."""
    add_rolling(services, not_before="11:00", not_after="11:00")

    worker.run_once()

    assert len(sender.sent) == 1


def test_a_session_before_not_before_is_not_pushed_for(worker, services, sender, saturday):
    add_rolling(services, not_before="12:00")

    worker.run_once()

    assert sender.sent == []


def test_a_session_after_not_after_is_not_pushed_for(worker, services, sender, saturday):
    add_rolling(services, not_after="10:00")

    worker.run_once()

    assert sender.sent == []


def test_the_right_time_on_the_wrong_day_is_still_filtered(
    worker, services, sender, saturday
):
    add_rolling(services, days=["sun"], not_before="09:00", not_after="13:00")

    worker.run_once()

    assert sender.sent == []


def test_a_watch_with_no_filters_is_unchanged(worker, services, sender, saturday):
    """Every row written before the filters existed reads as this one."""
    notification = add_rolling(services)

    worker.run_once()

    assert len(sender.sent) == 1
    assert set(stored(services, notification).notified_performances) == {"P1"}


def test_a_corrupt_days_column_skips_the_scan_rather_than_ignoring_the_filter(
    worker, services, sender, saturday
):
    """Scanning without it would push for exactly what the user excluded."""
    notification = add_rolling(services, days=["mon"])
    services.notifications.db.connection().execute(
        "UPDATE notifications SET days = 'someday' WHERE notification_id = ?",
        (notification.notification_id,),
    )
    services.notifications.db.connection().commit()

    worker.run_once()

    assert sender.sent == []
    assert stored(services, notification).next_check_at is not None


def test_a_corrupt_days_column_contributes_no_dates(services):
    notification = add_rolling(services, days=["sat"])
    assert Worker._scan_dates(replace(notification, days=[])) == []


def test_the_scan_skips_days_the_filter_excludes(monkeypatch, services):
    """A Saturdays-only watch must not pull Sunday's calendar to use none of it."""
    monkeypatch.setattr(clock, "now_utc", lambda: SATURDAY)
    notification = add_rolling(services, days=["sat"])
    assert Worker._scan_dates(notification) == ["2026-08-01"]


def test_an_unfiltered_scan_still_covers_the_whole_window(monkeypatch, services):
    monkeypatch.setattr(clock, "now_utc", lambda: SATURDAY)
    assert Worker._scan_dates(add_rolling(services)) == ["2026-08-01", "2026-08-02"]


def test_the_filters_survive_a_round_trip_through_the_row(services):
    notification = add_rolling(
        services, days=["SUN", "sat"], not_before="9:00", not_after="13:00"
    )
    row = stored(services, notification)
    assert row.days == ["sat", "sun"]
    assert (row.not_before, row.not_after) == ("09:00", "13:00")
