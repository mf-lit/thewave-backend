"""Wiring.

One place where settings become live collaborators, so the Flask app and the
admin CLI start the same way and tests can substitute a temporary database
without patching module globals.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from . import migrations
from .db import Database
from .directory import ClientDirectory
from .repository import MessageRepository
from .settings import ConfigFile, Settings

logger = logging.getLogger(__name__)


@dataclass
class Services:
    settings: Settings
    config: ConfigFile
    database: Database
    messages: MessageRepository
    directory: ClientDirectory

    @classmethod
    def build(cls, settings: Optional[Settings] = None) -> "Services":
        settings = settings or Settings.from_env()
        config = settings.config_file()

        database = Database(settings.db_path)
        version = migrations.apply(database)
        logger.info("Database %s ready at schema version %d", settings.db_path, version)

        return cls(
            settings=settings,
            config=config,
            database=database,
            messages=MessageRepository(database),
            # Not opened here: the upstream file may not exist yet, and a
            # directory that cannot be read is a degraded service, not a
            # startup failure. It connects lazily, per thread, on first use.
            directory=ClientDirectory(settings.upstream_db_path),
        )
