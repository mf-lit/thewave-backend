"""The check cycle."""
from __future__ import annotations

import pytest

from src.calendar_client import CalendarError
from src.models import NotificationRequest
from src.push import PushError
from src.worker import Worker
from tests.conftest import VALID_TOKEN, make_day, make_performance

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
