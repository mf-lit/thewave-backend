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
    elapsed_seconds: float
    delay_seconds: float

    def __str__(self) -> str:
        return (
            f"scraped {self.total} performances in {self.elapsed_seconds:.0f}s "
            f"(~{self.delay_seconds:.1f}s/perf): "
            f"{self.new} new, {self.changed} changed, "
            f"{self.unchanged} unchanged, {self.errored} errored"
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
    on_progress=None,
) -> CollectSummary:
    """Scrape ``days`` days of performances (from today) into ``db_path``.

    API calls are throttled so the whole pass takes roughly ``target_seconds``:
    the delay between performances is ``target_seconds / count``. ``on_progress``,
    if given, is called as ``on_progress(index, total, performance_ak, status)``.
    """
    start_date = start_date or dt.date.today()
    started = time.monotonic()
    counts: Counter[str] = Counter()

    with WaveScraper(proxy=proxy, locale=locale) as scraper:
        performances = scraper.list_calendar(start_date, days, categories)
        total = len(performances)
        delay = target_seconds / total if total else 0.0

        with PriceStore(db_path) as store:
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
        elapsed_seconds=time.monotonic() - started,
        delay_seconds=delay,
    )
