"""Who the clients are, read from upstream-api's database.

This service needs one fact it does not own: how long a client has been using
the app, so a message can target new installs (a welcome) or established ones
(a feature note). ``upstream-api`` already maintains it, in the ``clients``
table of ``water_temperature.db``, keyed on ``uuid``.

**It must not maintain its own first-seen table.** Such a table would be empty
on the day this feature ships, so every existing user would look brand-new and
receive the welcome message. Reading upstream-api's is the whole reason this
module exists.

Two structural decisions:

* Its **own** read-only connection, as ``dashboard/src/db.py`` opens one — not
  an ``ATTACH`` onto this service's write connection. A separate reader keeps
  the two files' lifecycles independent, and nothing here needs a cross-file
  join.
* It **degrades quietly**. That schema is managed by ``client_tracker.py``'s
  ad-hoc ``try: ALTER TABLE … except: pass``, so its shape is not guaranteed;
  a missing file, table or column returns None or nothing rather than raising.
  A client we cannot look up falls back to ``DEFAULT_DAYS_COUNT`` and still
  gets every message that does not target on the axis we lost.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional, Set

from .targeting import Client

logger = logging.getLogger(__name__)

TABLE = "clients"
ID_COLUMN = "uuid"
OPTIONAL_COLUMNS = ("days_count", "client_os", "client_version")


class ClientDirectory:
    """Read-only reader over upstream-api's ``clients`` table."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._local = threading.local()
        self._warned = False

    def _warn_once(self, reason: str) -> None:
        """One WARNING for the life of the process, not one per request.

        A missing upstream database is a persistent condition on a per-request
        path: logged every time, it would fill the container's output and, via
        log-alerts, ring a phone on a loop.
        """
        if not self._warned:
            self._warned = True
            logger.warning(
                "Client directory unavailable at %s (%s); "
                "targeting by days_count will fall back to the default",
                self.path,
                reason,
            )

    def _connection(self) -> Optional[sqlite3.Connection]:
        """This thread's read-only connection, or None if it cannot be opened.

        A failure is never cached: upstream-api may create the file after this
        process starts, and the next request should pick it up rather than
        needing a restart.
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        try:
            conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        except sqlite3.Error as exc:
            self._warn_once(str(exc))
            return None
        conn.row_factory = sqlite3.Row
        self._local.conn = conn
        return conn

    def _columns(self, conn: sqlite3.Connection) -> Set[str]:
        """The columns actually present, read fresh each call.

        Not cached: ``client_tracker`` adds columns with an ``ALTER TABLE`` at
        its own startup, which may land after this process opened its
        connection. ``PRAGMA table_info`` is answered from SQLite's in-memory
        schema, so asking every time costs nothing.
        """
        try:
            return {row["name"] for row in conn.execute(f"PRAGMA table_info({TABLE})")}
        except sqlite3.Error as exc:
            self._warn_once(str(exc))
            return set()

    def _usable_columns(self, conn: sqlite3.Connection) -> Optional[Set[str]]:
        """The columns present, or None when the table cannot be read at all.

        ``PRAGMA table_info`` on a table that does not exist returns no rows
        rather than raising, so an empty set is how a missing table arrives
        here — it is not an error path, and has to be checked for explicitly.
        """
        present = self._columns(conn)
        if not present:
            self._warn_once(f"no {TABLE} table")
            return None
        if ID_COLUMN not in present:
            self._warn_once(f"no {ID_COLUMN} column in {TABLE}")
            return None
        return present

    def _select(self, present: Set[str]) -> str:
        """A SELECT over whichever of the optional columns exist.

        Naming a column that was never added raises ``no such column`` and
        would cost us every other column too, so the statement is built from
        what is there. The names are a fixed whitelist, never caller input.
        """
        columns = [ID_COLUMN] + [name for name in OPTIONAL_COLUMNS if name in present]
        return f"SELECT {', '.join(columns)} FROM {TABLE}"

    @staticmethod
    def _client(row: sqlite3.Row) -> Client:
        """One row as targeting sees it, with anything absent left None.

        ``keys()`` rather than a fixed index: the statement's column list
        varies with what the upstream schema happens to have.
        """
        available = set(row.keys())
        return Client.build(
            client_id=row[ID_COLUMN],
            client_os=row["client_os"] if "client_os" in available else None,
            client_version=row["client_version"] if "client_version" in available else None,
            days_count=row["days_count"] if "days_count" in available else None,
        )

    def lookup(self, client_id: str) -> Optional[int]:
        """``days_count`` for one client, or None if it cannot be read.

        None means "unknown", which the caller reads as a brand-new client. It
        covers both a client genuinely absent from the table and every way the
        read itself can fail.
        """
        conn = self._connection()
        if conn is None:
            return None

        present = self._usable_columns(conn)
        if present is None:
            return None
        if "days_count" not in present:
            self._warn_once(f"no days_count column in {TABLE}")
            return None

        try:
            row = conn.execute(
                f"SELECT days_count FROM {TABLE} WHERE {ID_COLUMN} = ?", (client_id,)
            ).fetchone()
        except sqlite3.Error as exc:
            self._warn_once(str(exc))
            return None

        if row is None or row["days_count"] is None:
            return None
        return int(row["days_count"])

    def iter_clients(self) -> List[Client]:
        """Every known client, for the admin audience count.

        A list rather than a generator: the caller counts how many match a
        draft's rules, and the connection must not be left open across a lazy
        walk on a per-thread connection shared with ``lookup``.
        """
        conn = self._connection()
        if conn is None:
            return []

        present = self._usable_columns(conn)
        if present is None:
            return []

        try:
            rows = conn.execute(self._select(present)).fetchall()
        except sqlite3.Error as exc:
            self._warn_once(str(exc))
            return []

        return [self._client(row) for row in rows]

    def close(self) -> None:
        """Close this thread's connection, if it has one."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None
