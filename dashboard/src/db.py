"""Read-only SQLite access helpers.

All connections are opened with ``mode=ro`` so the dashboard can never modify
the source databases. ``connect_upstream`` optionally ATTACHes the
notifications database so queries can join across both files.
"""

import os
import sqlite3
from contextlib import contextmanager

from . import config
from .cloud_ips import is_cloud_ip


def _connect_ro(path: str) -> sqlite3.Connection:
    """Open ``path`` read-only. Raises if the file is missing/unreadable."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # SQLite has no native CIDR matching; expose the Python classifier as a
    # scalar function so Google/Apple IPs can be filtered inside SQL.
    conn.create_function("is_cloud_ip", 1, is_cloud_ip, deterministic=True)
    return conn


@contextmanager
def upstream(attach_notifications: bool = False, attach_active: bool = False):
    """Connection to water_temperature.db.

    When ``attach_notifications`` is True, notifications.db is attached as the
    schema ``notif`` (read-only), enabling cross-database joins such as
    ``notif.notifications`` LEFT JOIN ``clients``.

    When ``attach_active`` is True and the daily-active store exists, it is
    attached read-only as ``hist`` (for the active-clients chart merge).
    """
    conn = _connect_ro(config.UPSTREAM_DB_PATH)
    try:
        if attach_notifications:
            conn.execute(
                "ATTACH DATABASE ? AS notif",
                (f"file:{config.NOTIFICATIONS_DB_PATH}?mode=ro",),
            )
        if attach_active and os.path.exists(config.DAILY_ACTIVE_DB_PATH):
            conn.execute(
                "ATTACH DATABASE ? AS hist",
                (f"file:{config.DAILY_ACTIVE_DB_PATH}?mode=ro",),
            )
        yield conn
    finally:
        conn.close()


def active_store_exists() -> bool:
    """Whether the daily-active snapshot store has been created yet."""
    return os.path.exists(config.DAILY_ACTIVE_DB_PATH)


@contextmanager
def notifications():
    """Connection to notifications.db on its own."""
    conn = _connect_ro(config.NOTIFICATIONS_DB_PATH)
    try:
        yield conn
    finally:
        conn.close()
