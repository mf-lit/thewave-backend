"""SQLite connection management.

Copied from ``notifications/src/db.py``, which solved the same problem: two
gunicorn workers, threaded, sharing one database file. WAL journalling, a busy
timeout, and one connection per thread rather than a module-level connection
handed to every request.

Writes go through ``transaction()``, which opens ``BEGIN IMMEDIATE`` up front
to take the write lock in one step rather than deadlocking on an upgrade from
a read transaction.

This covers only this service's own file. The read-only reader over
upstream-api's database is ``directory.py``, deliberately separate — see the
note there.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

BUSY_TIMEOUT_MS = 5000


class Database:
    """A SQLite file, with lazily-created per-thread connections."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    def connection(self) -> sqlite3.Connection:
        """The calling thread's connection, opening one if needed."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            self._local.conn = conn
        return conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a write transaction, committing on success."""
        conn = self.connection()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.rollback()
            raise
        conn.commit()

    def close(self) -> None:
        """Close this thread's connection, if it has one."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
