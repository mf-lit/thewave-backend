"""SQLite storage for scraped performance prices, with per-performance history.

One row per performance (keyed by ``performanceAK``). On each run we record the
current min/max price; whenever either changes versus the stored row we append a
snapshot to that row's ``history`` (a JSON list).
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import dataclass

_SCHEMA = """
CREATE TABLE IF NOT EXISTS performances (
    performanceAK       TEXT PRIMARY KEY,
    price_min           REAL,
    price_max           REAL,
    date_first_scraped  TEXT NOT NULL,
    date_last_scraped   TEXT NOT NULL,
    history             TEXT NOT NULL
);
"""


@dataclass
class RecordResult:
    """Outcome of a single :meth:`PriceStore.record` call."""

    performance_ak: str
    status: str  # "new" | "unchanged" | "changed"


def _iso(when: dt.datetime) -> str:
    return when.isoformat(timespec="seconds")


class PriceStore:
    """A thin SQLite wrapper for recording performance prices over time.

    Usable as a context manager::

        with PriceStore("prices.db") as store:
            store.record("TWB.EVN17.PRF1687", 59.0, 65.0, datetime.now())
    """

    def __init__(self, db_path: str) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def __enter__(self) -> "PriceStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    def record(
        self,
        performance_ak: str,
        price_min: float | None,
        price_max: float | None,
        now: dt.datetime,
    ) -> RecordResult:
        """Insert or update one performance; append history on a price change.

        Returns a :class:`RecordResult` with status ``new``, ``unchanged`` or
        ``changed``. ``now`` is supplied by the caller so behaviour is testable.
        """
        ts = _iso(now)
        row = self.conn.execute(
            "SELECT price_min, price_max, history FROM performances WHERE performanceAK = ?",
            (performance_ak,),
        ).fetchone()

        snapshot = {"price_min": price_min, "price_max": price_max, "date": ts}

        if row is None:
            self.conn.execute(
                "INSERT INTO performances "
                "(performanceAK, price_min, price_max, date_first_scraped, date_last_scraped, history) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (performance_ak, price_min, price_max, ts, ts, json.dumps([snapshot])),
            )
            self.conn.commit()
            return RecordResult(performance_ak, "new")

        changed = row["price_min"] != price_min or row["price_max"] != price_max
        if not changed:
            self.conn.execute(
                "UPDATE performances SET date_last_scraped = ? WHERE performanceAK = ?",
                (ts, performance_ak),
            )
            self.conn.commit()
            return RecordResult(performance_ak, "unchanged")

        history = json.loads(row["history"])
        history.append(snapshot)
        self.conn.execute(
            "UPDATE performances SET price_min = ?, price_max = ?, "
            "date_last_scraped = ?, history = ? WHERE performanceAK = ?",
            (price_min, price_max, ts, json.dumps(history), performance_ak),
        )
        self.conn.commit()
        return RecordResult(performance_ak, "changed")
