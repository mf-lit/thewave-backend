"""Wiring.

One place where settings become live collaborators, so both entry points (the
Flask app and the worker) start the same way, and tests can substitute a fake
calendar or push sender without patching module globals.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from . import migrations
from .calendar_client import CalendarClient
from .db import Database
from .push import FirebaseSender, Notifier, Sender
from .repository import ClientRepository, NotificationRepository
from .settings import ConfigFile, Settings

logger = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    config: ConfigFile
    database: Database
    notifications: NotificationRepository
    clients: ClientRepository
    calendar: CalendarClient
    notifier: Notifier

    @classmethod
    def build(
        cls,
        settings: Optional[Settings] = None,
        calendar: Optional[CalendarClient] = None,
        sender: Optional[Sender] = None,
    ) -> "Services":
        settings = settings or Settings.from_env()
        config = settings.config_file()

        database = Database(settings.db_path)
        version = migrations.apply(database)
        logger.info("Database %s ready at schema version %d", settings.db_path, version)

        clients = ClientRepository(database)
        return cls(
            settings=settings,
            config=config,
            database=database,
            notifications=NotificationRepository(database),
            clients=clients,
            calendar=calendar
            or CalendarClient(
                base_url=settings.calendar_api_url,
                api_key=lambda: settings.calendar_api_key_env or config.calendar_api_key(),
            ),
            notifier=Notifier(
                clients, sender or FirebaseSender(settings.credentials_path)
            ),
        )
