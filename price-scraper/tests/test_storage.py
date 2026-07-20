"""Tests for PriceStore record/history behaviour (no network needed)."""

import datetime as dt
import json

from price_scraper.storage import PriceStore


def _store(tmp_path):
    return PriceStore(str(tmp_path / "test.db"))


def _row(store, ak):
    return store.conn.execute(
        "SELECT * FROM performances WHERE performanceAK = ?", (ak,)
    ).fetchone()


def test_new_then_unchanged_then_changed(tmp_path):
    ak = "TWB.EVN17.PRF1687"
    t0 = dt.datetime(2026, 6, 6, 9, 0, 0)
    t1 = dt.datetime(2026, 6, 7, 9, 0, 0)
    t2 = dt.datetime(2026, 6, 8, 9, 0, 0)

    with _store(tmp_path) as store:
        # 1) first sighting -> new, history seeded with one snapshot
        r = store.record(ak, 59.0, 65.0, t0)
        assert r.status == "new"
        row = _row(store, ak)
        assert (row["price_min"], row["price_max"]) == (59.0, 65.0)
        assert row["date_first_scraped"] == row["date_last_scraped"] == t0.isoformat(timespec="seconds")
        assert len(json.loads(row["history"])) == 1

        # 2) same price -> unchanged, history untouched, last_scraped advances
        r = store.record(ak, 59.0, 65.0, t1)
        assert r.status == "unchanged"
        row = _row(store, ak)
        assert row["date_first_scraped"] == t0.isoformat(timespec="seconds")
        assert row["date_last_scraped"] == t1.isoformat(timespec="seconds")
        assert len(json.loads(row["history"])) == 1

        # 3) price change -> changed, history appended, values updated
        r = store.record(ak, 53.0, 65.0, t2)
        assert r.status == "changed"
        row = _row(store, ak)
        assert (row["price_min"], row["price_max"]) == (53.0, 65.0)
        assert row["date_last_scraped"] == t2.isoformat(timespec="seconds")
        history = json.loads(row["history"])
        assert len(history) == 2
        assert history[-1] == {"price_min": 53.0, "price_max": 65.0, "date": t2.isoformat(timespec="seconds")}


def test_max_only_change_counts_as_changed(tmp_path):
    ak = "TWB.EVN1.PRF1"
    with _store(tmp_path) as store:
        store.record(ak, 59.0, 65.0, dt.datetime(2026, 6, 6, 9))
        r = store.record(ak, 59.0, 71.0, dt.datetime(2026, 6, 7, 9))
        assert r.status == "changed"
        assert len(json.loads(_row(store, ak)["history"])) == 2
