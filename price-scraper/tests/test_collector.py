"""Tests for collect() throttling, the --max-delay cap, and DB-skip behaviour.

The network-bound WaveScraper is replaced with an in-memory fake, and
``time.sleep`` is patched out so the throttle is exercised without real waits.
"""

import datetime as dt

import pytest

from price_scraper import collector


class FakeScraper:
    """Stand-in for WaveScraper: serves a fixed performance list and prices."""

    def __init__(self, performances, **_kwargs):
        self._performances = performances
        self.priced = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def list_calendar(self, _start_date, _num_days, _categories):
        return list(self._performances)

    def price_range(self, ak):
        self.priced.append(ak)
        return (10.0, 20.0)


def _perfs(n):
    return [{"performance_ak": f"TWB.EVN1.PRF{i}"} for i in range(n)]


@pytest.fixture
def patched(monkeypatch):
    """Patch collector.WaveScraper with a FakeScraper and record sleep calls."""
    sleeps = []
    monkeypatch.setattr(collector.time, "sleep", lambda s: sleeps.append(s))

    def install(performances):
        scraper = FakeScraper(performances)
        monkeypatch.setattr(collector, "WaveScraper", lambda **kw: scraper)
        return scraper

    install.sleeps = sleeps
    return install


def _collect(tmp_path, *, target_seconds, max_delay_seconds=None, days=7):
    return collector.collect(
        days=days,
        target_seconds=target_seconds,
        db_path=str(tmp_path / "prices.db"),
        max_delay_seconds=max_delay_seconds,
    )


def test_uncapped_delay_is_target_over_count(tmp_path, patched):
    patched(_perfs(10))
    summary = _collect(tmp_path, target_seconds=20.0)
    assert summary.delay_seconds == pytest.approx(2.0)


def test_max_delay_caps_the_delay(tmp_path, patched):
    patched(_perfs(10))  # uncapped would be 1000 / 10 == 100s
    summary = _collect(tmp_path, target_seconds=1000.0, max_delay_seconds=5.0)
    assert summary.delay_seconds == pytest.approx(5.0)


def test_cap_above_natural_delay_is_a_noop(tmp_path, patched):
    patched(_perfs(10))  # natural delay 20 / 10 == 2s, well under the 100s cap
    summary = _collect(tmp_path, target_seconds=20.0, max_delay_seconds=100.0)
    assert summary.delay_seconds == pytest.approx(2.0)


def test_no_sleep_exceeds_the_cap(tmp_path, patched):
    install = patched
    install(_perfs(10))
    _collect(tmp_path, target_seconds=1000.0, max_delay_seconds=5.0)
    # Throttle sleeps between performances (total - 1 times), none over the cap.
    assert install.sleeps  # at least one sleep happened
    assert max(install.sleeps) <= 5.0


def test_cap_with_zero_performances_is_safe(tmp_path, patched):
    patched(_perfs(0))
    summary = _collect(tmp_path, target_seconds=1000.0, max_delay_seconds=5.0)
    assert summary.total == 0
    assert summary.delay_seconds == 0.0
