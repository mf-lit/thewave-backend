"""Orchestrate a throttled scrape of N days of performances into SQLite."""

from __future__ import annotations

import datetime as dt
import time
from collections import Counter
from dataclasses import dataclass

from .scraper import DEFAULT_EVENT_CATEGORIES, DEFAULT_LOCALE, WaveScraper
from .storage import PriceStore


@dataclass
class CollectSummary:
    total: int
    new: int
    changed: int
    unchanged: int
    errored: int
    skipped: int
    elapsed_seconds: float
    delay_seconds: float

    def __str__(self) -> str:
        return (
            f"scraped {self.total} performances in {self.elapsed_seconds:.0f}s "
            f"(~{self.delay_seconds:.1f}s/perf): "
            f"{self.new} new, {self.changed} changed, "
            f"{self.unchanged} unchanged, {self.errored} errored, "
            f"{self.skipped} skipped (already in db)"
        )


def collect(
    *,
    days: int,
    target_seconds: float,
    db_path: str,
    start_date: dt.date | None = None,
    proxy: str | None = None,
    locale: str = DEFAULT_LOCALE,
    categories: tuple[str, ...] = DEFAULT_EVENT_CATEGORIES,
    overwrite: bool = False,
    max_delay_seconds: float | None = None,
    on_progress=None,
) -> CollectSummary:
    """Scrape ``days`` days of performances (from today) into ``db_path``.

    API calls are throttled so the whole pass takes roughly ``target_seconds``:
    the delay between performances is ``target_seconds / count``. If
    ``max_delay_seconds`` is given, the inter-performance delay is capped at that
    value (so a small batch finishes sooner than ``target_seconds``).
    ``on_progress``, if given, is called as
    ``on_progress(index, total, performance_ak, status)``.

    By default, performances already present in the DB are skipped. Pass
    ``overwrite=True`` to re-scrape every performance regardless.
    """
    start_date = start_date or dt.date.today()
    started = time.monotonic()
    counts: Counter[str] = Counter()

    with WaveScraper(proxy=proxy, locale=locale) as scraper:
        performances = scraper.list_calendar(start_date, days, categories)

        with PriceStore(db_path) as store:
            if not overwrite:
                existing = store.existing_aks()
                kept = [p for p in performances if p["performance_ak"] not in existing]
                counts["skipped"] = len(performances) - len(kept)
                performances = kept

            total = len(performances)
            delay = target_seconds / total if total else 0.0
            if max_delay_seconds is not None:
                delay = min(delay, max_delay_seconds)

            for index, perf in enumerate(performances, start=1):
                ak = perf["performance_ak"]
                call_started = time.monotonic()
                try:
                    price_min, price_max = scraper.price_range(ak)
                    result = store.record(ak, price_min, price_max, dt.datetime.now())
                    counts[result.status] += 1
                    status = result.status
                except Exception as exc:  # keep going across the whole pass
                    counts["errored"] += 1
                    status = f"error: {exc}"
                if on_progress is not None:
                    on_progress(index, total, ak, status)
                # Throttle: spread calls evenly, accounting for the call's own time.
                if index < total and delay:
                    elapsed = time.monotonic() - call_started
                    if delay > elapsed:
                        time.sleep(delay - elapsed)

    return CollectSummary(
        total=total,
        new=counts["new"],
        changed=counts["changed"],
        unchanged=counts["unchanged"],
        errored=counts["errored"],
        skipped=counts["skipped"],
        elapsed_seconds=time.monotonic() - started,
        delay_seconds=delay,
    )
