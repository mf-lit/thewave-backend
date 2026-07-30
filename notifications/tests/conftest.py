"""Shared fixtures.

Everything runs against a temporary database with a fake calendar and a fake
push sender, so the suite needs no network, no Firebase credentials, and never
touches the production file.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Dict, List, Optional

import pytest
import yaml

from src.api.app import create_app
from src.calendar_client import CalendarData
from src.push import PushMessage
from src.services import Services
from src.settings import Settings

API_KEY = "test-api-key-0123456789"
VALID_TOKEN = "d" * 60 + ":" + "A" * 100  # 161 chars, allowed alphabet


def make_performance(
    performance_ak: str = "TWB.EVN1.PRF1",
    title: str = "Advanced Surf",
    time: str = "18:00",
    availability: Optional[Dict[str, int]] = None,
) -> Dict:
    """A performance as the upstream calendar API returns it."""
    seats = availability if availability is not None else {"left": 5, "right": 5}
    return {
        "performanceAK": performance_ak,
        "time": time,
        "fields": {"title": title},
        "availabilityPerProduct": [
            {"side": side, "availability": {"available": count}}
            for side, count in seats.items()
        ],
    }


def make_day(date: str, performances: List[Dict]) -> Dict:
    return {"date": date, "performances": performances}


class FakeCalendar:
    """Stands in for CalendarClient; records what was asked for."""

    def __init__(self, days: Optional[List[Dict]] = None):
        self.days = days or []
        self.error: Optional[Exception] = None
        self.requested_dates: List[List[str]] = []

    def _data(self, wanted: List[str]) -> CalendarData:
        if self.error:
            raise self.error
        return CalendarData(days=[d for d in self.days if d["date"] in set(wanted)])

    def fetch_day(self, date: str) -> CalendarData:
        self.requested_dates.append([date])
        return self._data([date])

    def fetch_dates(self, dates) -> CalendarData:
        self.requested_dates.append(list(dates))
        return self._data(list(dates))


class FakeSender:
    """Collects messages instead of sending them."""

    def __init__(self):
        self.sent: List[PushMessage] = []
        self.error: Optional[Exception] = None

    def send(self, message: PushMessage) -> str:
        if self.error:
            raise self.error
        self.sent.append(message)
        return f"projects/test/messages/{len(self.sent)}"


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"api_keys": [API_KEY], "calendar_api_key": "cal-key"}))
    return path


@pytest.fixture
def settings(tmp_path: Path, config_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "notifications.db",
        config_path=config_path,
        credentials_path=tmp_path / "credentials.json",
        calendar_api_url="http://calendar.invalid/calendar",
        calendar_api_key_env=None,
        auth_disabled=False,
        heartbeat_path=tmp_path / "heartbeat",
    )


@pytest.fixture
def calendar() -> FakeCalendar:
    return FakeCalendar()


@pytest.fixture
def sender() -> FakeSender:
    return FakeSender()


@pytest.fixture
def services(settings: Settings, calendar: FakeCalendar, sender: FakeSender) -> Services:
    return Services.build(settings=settings, calendar=calendar, sender=sender)


@pytest.fixture
def app(services: Services):
    application = create_app(services=services)
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def auth() -> Dict[str, str]:
    return {"x-api-key": API_KEY}


@pytest.fixture
def client_id() -> str:
    return str(uuid.uuid4())
