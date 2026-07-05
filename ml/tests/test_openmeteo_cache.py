"""Open-Meteo cache test: second invocation does not call the network."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd


def test_archive_uses_cache(monkeypatch, tmp_path: Path) -> None:
    from lake_forecast import config as cfgmod
    from lake_forecast.io import openmeteo

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setattr(openmeteo, "_cache_dir", lambda: cache_dir)

    cfg = cfgmod.data_config()
    variables = list(cfg["openmeteo"]["variables"])
    start = datetime(2024, 1, 1, tzinfo=UTC)
    end = datetime(2024, 1, 5, tzinfo=UTC)

    call_count = {"n": 0}

    def _fake_request(url: str, params):
        call_count["n"] += 1
        sd = pd.Timestamp(params["start_date"], tz="UTC")
        ed = pd.Timestamp(params["end_date"], tz="UTC") + pd.Timedelta(days=1)
        idx = pd.date_range(sd, ed, freq="h", tz="UTC", inclusive="left")
        payload = {"hourly": {"time": [t.isoformat() for t in idx]}}
        for v in variables:
            payload["hourly"][v] = [0.0] * len(idx)
        return payload

    monkeypatch.setattr(openmeteo, "_request", _fake_request)

    df1 = openmeteo.fetch_archive(start, end)
    assert call_count["n"] >= 1
    first_calls = call_count["n"]

    df2 = openmeteo.fetch_archive(start, end)
    assert call_count["n"] == first_calls, "second invocation should be served from cache"
    assert len(df1) == len(df2)
    assert set(variables).issubset(df2.columns)
