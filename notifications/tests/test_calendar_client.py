"""Upstream calendar access."""
from __future__ import annotations

import json

import pytest
import requests

from src.calendar_client import (
    CalendarClient,
    CalendarData,
    CalendarError,
    availability_for_side,
    group_consecutive,
    matching_sessions,
    performance_title,
)
from tests.conftest import make_day, make_performance


class FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload if payload is not None else {"days": []}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        if not self.responses:
            return FakeResponse()
        return self.responses.pop(0)


def client(session, api_key="secret") -> CalendarClient:
    return CalendarClient(
        "http://calendar.invalid/calendar", api_key=lambda: api_key, session=session
    )


# -- request shaping ----------------------------------------------------------

def test_single_day_request():
    session = FakeSession()
    client(session).fetch_day("2026-08-05")

    (call,) = session.calls
    assert call["params"] == {"dateFrom": "2026-08-05", "numberOfDays": 1}
    assert call["headers"] == {"x-api-key": "secret"}


def test_the_api_key_is_read_per_request_so_rotation_takes_effect():
    session = FakeSession()
    keys = iter(["first", "second"])
    calendar = CalendarClient(
        "http://calendar.invalid/calendar", api_key=lambda: next(keys), session=session
    )

    calendar.fetch_day("2026-08-05")
    calendar.fetch_day("2026-08-06")
    assert [call["headers"]["x-api-key"] for call in session.calls] == ["first", "second"]


def test_no_header_when_no_key_is_configured():
    session = FakeSession()
    CalendarClient("http://calendar.invalid/calendar", session=session).fetch_day("2026-08-05")
    assert session.calls[0]["headers"] == {}


# -- date batching ------------------------------------------------------------

@pytest.mark.parametrize(
    "dates, expected",
    [
        ([], []),
        (["2026-08-05"], [["2026-08-05"]]),
        (["2026-08-05", "2026-08-06"], [["2026-08-05", "2026-08-06"]]),
        (["2026-08-05", "2026-08-09"], [["2026-08-05"], ["2026-08-09"]]),
        (
            ["2026-08-05", "2026-08-06", "2026-08-09"],
            [["2026-08-05", "2026-08-06"], ["2026-08-09"]],
        ),
        (["2026-08-31", "2026-09-01"], [["2026-08-31", "2026-09-01"]]),
        (["2026-02-28", "2026-03-01"], [["2026-02-28", "2026-03-01"]]),
    ],
)
def test_consecutive_dates_are_grouped(dates, expected):
    assert group_consecutive(dates) == expected


def test_consecutive_dates_cost_one_request():
    session = FakeSession()
    client(session).fetch_dates(["2026-08-05", "2026-08-06", "2026-08-07"])

    assert len(session.calls) == 1
    assert session.calls[0]["params"] == {"dateFrom": "2026-08-05", "numberOfDays": 3}


def test_gaps_split_into_separate_requests():
    session = FakeSession()
    client(session).fetch_dates(["2026-08-05", "2026-08-20"])

    assert [call["params"]["dateFrom"] for call in session.calls] == ["2026-08-05", "2026-08-20"]


def test_duplicate_dates_are_collapsed():
    session = FakeSession()
    client(session).fetch_dates(["2026-08-05", "2026-08-05"])
    assert session.calls[0]["params"] == {"dateFrom": "2026-08-05", "numberOfDays": 1}


def test_no_dates_means_no_request():
    session = FakeSession()
    assert client(session).fetch_dates([]).days == []
    assert session.calls == []


def test_days_outside_the_requested_set_are_discarded():
    """A range request returns the gap days too; they are not wanted."""
    payload = {
        "days": [
            make_day("2026-08-05", []),
            make_day("2026-08-06", []),
            make_day("2026-08-07", []),
        ]
    }
    session = FakeSession([FakeResponse(payload)])
    data = client(session).fetch_dates(["2026-08-05", "2026-08-07"])
    assert [day["date"] for day in data.days] == ["2026-08-05", "2026-08-07"]


# -- failures -----------------------------------------------------------------

def test_http_errors_become_calendar_errors():
    session = FakeSession([FakeResponse(status=503)])
    with pytest.raises(CalendarError):
        client(session).fetch_day("2026-08-05")


def test_unparseable_json_becomes_a_calendar_error():
    session = FakeSession([FakeResponse(json.JSONDecodeError("bad", "", 0))])
    with pytest.raises(CalendarError):
        client(session).fetch_day("2026-08-05")


def test_connection_failures_become_calendar_errors():
    class Broken:
        def get(self, *args, **kwargs):
            raise requests.ConnectionError("refused")

    with pytest.raises(CalendarError):
        client(Broken()).fetch_day("2026-08-05")


# -- reading the payload ------------------------------------------------------

def test_finding_a_performance_across_days():
    session = FakeSession(
        [
            FakeResponse(
                {
                    "days": [
                        make_day("2026-08-05", [make_performance(performance_ak="A")]),
                        make_day("2026-08-06", [make_performance(performance_ak="B")]),
                    ]
                }
            )
        ]
    )
    data = client(session).fetch_dates(["2026-08-05", "2026-08-06"])

    assert data.find_performance("B")["performanceAK"] == "B"
    assert data.find_performance("MISSING") is None


def test_availability_by_side():
    performance = make_performance(availability={"left": 4, "right": 0})
    assert availability_for_side(performance, "left") == 4
    assert availability_for_side(performance, "right") == 0
    assert availability_for_side(performance, "none") is None


def test_title_extraction():
    assert performance_title(make_performance(title="Improver Lesson")) == "Improver Lesson"
    assert performance_title({}) == ""


# -- matching_sessions --------------------------------------------------------

def rolling_day(date="2026-08-05"):
    return make_day(
        date,
        [
            make_performance("P1", "Advanced Surf", "18:00:00.000", {"left": 2, "right": 7}),
            make_performance("P2", "Advanced Surf", "10:00", {"right": 4}),
            make_performance("P3", "Advanced Surf Lesson", "11:00", {"right": 9}),
            make_performance("P4", "Advanced Coaching (In Water)", "12:00", {"right": 9}),
            make_performance("P5", "Advanced Surf", "13:00", {"left": 9}),
        ],
    )


def test_matching_sessions_finds_every_performance_with_that_title_and_side():
    found = matching_sessions(CalendarData(days=[rolling_day()]), "Advanced Surf", "right")
    assert [s.performance_ak for s in found] == ["P2", "P1"]


def test_matching_sessions_returns_them_soonest_first():
    days = [rolling_day("2026-08-06"), rolling_day("2026-08-05")]
    found = matching_sessions(CalendarData(days=days), "Advanced Surf", "right")
    assert [(s.date, s.time) for s in found] == [
        ("2026-08-05", "10:00"),
        ("2026-08-05", "18:00"),
        ("2026-08-06", "10:00"),
        ("2026-08-06", "18:00"),
    ]


def test_a_session_carries_the_day_it_was_returned_under():
    """`performance["date"]` is not relied on; `fetch_dates` filters on the day key."""
    found = matching_sessions(CalendarData(days=[rolling_day("2026-09-01")]), "Advanced Surf", "right")
    assert {s.date for s in found} == {"2026-09-01"}


def test_matching_sessions_normalises_the_upstream_time():
    found = matching_sessions(CalendarData(days=[rolling_day()]), "Advanced Surf", "right")
    assert [s.time for s in found] == ["10:00", "18:00"]


def test_matching_sessions_reports_the_availability_for_the_side_asked_for():
    right = matching_sessions(CalendarData(days=[rolling_day()]), "Advanced Surf", "right")
    left = matching_sessions(CalendarData(days=[rolling_day()]), "Advanced Surf", "left")
    assert {s.performance_ak: s.availability for s in right} == {"P1": 7, "P2": 4}
    assert {s.performance_ak: s.availability for s in left} == {"P1": 2, "P5": 9}


@pytest.mark.parametrize("given", ["advanced surf", "ADVANCED SURF", "  Advanced Surf  "])
def test_matching_sessions_folds_case_and_surrounding_space(given):
    found = matching_sessions(CalendarData(days=[rolling_day()]), given, "right")
    assert [s.performance_ak for s in found] == ["P2", "P1"]


@pytest.mark.parametrize("title", ["Advanced", "Advanced Surf Lesson", "Surf"])
def test_matching_sessions_never_matches_on_a_prefix_or_substring(title):
    found = matching_sessions(CalendarData(days=[rolling_day()]), title, "right")
    assert [s.performance_ak for s in found] != ["P2", "P1"]


def test_a_neighbouring_title_is_not_pulled_in():
    found = matching_sessions(CalendarData(days=[rolling_day()]), "Advanced Surf", "right")
    assert {"P3", "P4"}.isdisjoint({s.performance_ak for s in found})


def test_matching_sessions_keeps_the_upstream_casing_for_display():
    days = [make_day("2026-08-05", [make_performance("P1", "Advanced Surf", "10:00", {"right": 9})])]
    found = matching_sessions(CalendarData(days=days), "advanced surf", "right")
    assert found[0].title == "Advanced Surf"


@pytest.mark.parametrize(
    "performance",
    [
        make_performance("", "Advanced Surf", "10:00", {"right": 9}),
        make_performance("P9", "Advanced Surf", "not-a-time", {"right": 9}),
        make_performance("P9", "Advanced Surf", "10:00", {"left": 9}),
    ],
)
def test_matching_sessions_skips_what_it_cannot_use(performance):
    days = [make_day("2026-08-05", [performance])]
    assert matching_sessions(CalendarData(days=days), "Advanced Surf", "right") == []


def test_matching_sessions_refuses_an_empty_title():
    """An all-whitespace title would otherwise match every untitled performance."""
    assert matching_sessions(CalendarData(days=[rolling_day()]), "   ", "right") == []


def test_matching_sessions_skips_a_day_without_a_date():
    days = [{"performances": [make_performance("P1", "Advanced Surf", "10:00", {"right": 9})]}]
    assert matching_sessions(CalendarData(days=days), "Advanced Surf", "right") == []
