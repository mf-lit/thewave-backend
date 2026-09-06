"""Shared fixtures.

Everything runs against a temporary database and a temporary config file, so
the suite needs no network and never touches the production file.
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Sequence

import pytest
import yaml

from src.api.app import create_app
from src.models import Message
from src.services import Services
from src.settings import Settings

API_KEY = "test-api-key-0123456789"
ADMIN_KEY = "test-admin-key-9876543210"

# A fixed "now" for the targeting tests, so a window either contains it or does
# not and nothing depends on when the suite runs.
NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
LIVE_SINCE = "2026-01-01T00:00:00+00:00"

# The columns upstream-api's `clients` table has today. `client_tracker` builds
# it with ad-hoc ALTERs, so tests that care about a missing one pass a subset.
UPSTREAM_COLUMNS: Sequence[str] = (
    "uuid",
    "first_seen",
    "last_seen",
    "request_count",
    "days_count",
    "client_os",
    "client_version",
)


def make_message(**overrides: Any) -> Message:
    """A live, untargeted message, with fields overridden.

    Every targeting field defaults to None — no constraint — so a test that
    sets one is testing that axis and nothing else.
    """
    fields: Dict[str, Any] = dict(
        message_id="message-1",
        title="Lagoon closed Tuesday",
        body="The lagoon is closed for maintenance.",
        client_ids=None,
        os=None,
        min_version=None,
        max_version=None,
        min_days_count=None,
        max_days_count=None,
        starts_at=LIVE_SINCE,
        ends_at=None,
        retain=False,
        expires_at=None,
        display="banner",
        level="info",
        priority=0,
        action_url=None,
        action_label=None,
        enabled=True,
        revision=1,
        created_at=LIVE_SINCE,
        updated_at=LIVE_SINCE,
    )
    fields.update(overrides)
    return Message(**fields)


def build_upstream_db(
    path: Path,
    rows: Iterable[Dict[str, Any]] = (),
    columns: Sequence[str] = UPSTREAM_COLUMNS,
    table: str = "clients",
) -> Path:
    """A throwaway water_temperature.db holding just what the directory reads.

    ``columns`` and ``table`` are overridable so the degradation tests can
    build the shapes that ad-hoc schema management actually produces.
    """
    conn = sqlite3.connect(path)
    conn.execute(f"CREATE TABLE {table} ({', '.join(columns)})")
    for row in rows:
        present = [name for name in columns if name in row]
        conn.execute(
            f"INSERT INTO {table} ({', '.join(present)}) "
            f"VALUES ({', '.join('?' for _ in present)})",
            [row[name] for name in present],
        )
    conn.commit()
    conn.close()
    return path


def make_payload(**overrides: Any) -> Dict[str, Any]:
    """A minimal valid ``POST /admin/messages`` body, with fields overridden.

    Only the three required fields, so a test that overrides one of the
    optional ones is testing that field and nothing else.
    """
    payload: Dict[str, Any] = {
        "title": "Lagoon closed Tuesday",
        "body": "The lagoon is closed for maintenance.",
        "display": "banner",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        yaml.safe_dump({"api_keys": [API_KEY], "admin_keys": [ADMIN_KEY]})
    )
    return path


@pytest.fixture
def settings(tmp_path: Path, config_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "messages.db",
        config_path=config_path,
        upstream_db_path=tmp_path / "water_temperature.db",
        auth_disabled=False,
        ttl_seconds=900,
    )


@pytest.fixture
def services(settings: Settings) -> Services:
    return Services.build(settings=settings)


@pytest.fixture
def repository(services: Services):
    return services.messages


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
def admin_auth() -> Dict[str, str]:
    return {"x-admin-key": ADMIN_KEY}


@pytest.fixture
def client_id() -> str:
    return str(uuid.uuid4())
