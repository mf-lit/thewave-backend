"""Cached Open-Meteo client (archive + forecast).

Cache strategy:
  * Historical archive is immutable past T-5d, so archive responses are split
    into 90-day "buckets" and parquet-cached forever.
  * Forecast responses have a 1-hour TTL.

All API requests are wrapped with tenacity exponential backoff on 429/5xx
and transient transport errors (DNS/connect/read failures).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from lake_forecast.config import data_config, repo_path


class OpenMeteoError(RuntimeError):
    pass


class _RetryableHTTPError(RuntimeError):
    pass


@dataclass(frozen=True)
class SiteSpec:
    latitude: float
    longitude: float


def _site_from_config() -> SiteSpec:
    cfg = data_config()
    return SiteSpec(latitude=float(cfg["site"]["latitude"]), longitude=float(cfg["site"]["longitude"]))


def _cache_dir() -> Path:
    cfg = data_config()
    p = repo_path(cfg["paths"]["weather_cache_dir"])
    p.mkdir(parents=True, exist_ok=True)
    return p


def _retry_cfg() -> dict[str, Any]:
    return data_config()["openmeteo"]["retry"]


def _params_hash(params: dict[str, Any]) -> str:
    s = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def _request(url: str, params: dict[str, Any]) -> dict[str, Any]:
    rc = _retry_cfg()

    @retry(
        retry=retry_if_exception_type((_RetryableHTTPError, httpx.TransportError)),
        stop=stop_after_attempt(int(rc["attempts"])),
        wait=wait_exponential(multiplier=float(rc["base_seconds"]), max=float(rc["max_seconds"])),
        reraise=True,
    )
    def _do() -> dict[str, Any]:
        with httpx.Client(timeout=60.0) as client:
            resp = client.get(url, params=params)
            if resp.status_code in (429, 500, 502, 503, 504):
                raise _RetryableHTTPError(f"{resp.status_code} from {url}: {resp.text[:200]}")
            if resp.status_code >= 400:
                raise OpenMeteoError(f"{resp.status_code} from {url}: {resp.text[:500]}")
            return resp.json()

    return _do()


def _hourly_to_frame(payload: dict[str, Any], variables: Iterable[str]) -> pd.DataFrame:
    hourly = payload.get("hourly")
    if not hourly or "time" not in hourly:
        raise OpenMeteoError("Open-Meteo response missing 'hourly.time'")
    times = pd.to_datetime(hourly["time"], utc=True)
    data = {"time": times}
    for var in variables:
        if var not in hourly:
            raise OpenMeteoError(f"Open-Meteo response missing variable '{var}'")
        data[var] = hourly[var]
    df = pd.DataFrame(data).set_index("time").sort_index()
    df.index = df.index.floor("h")
    df = df[~df.index.duplicated(keep="first")]
    return df


def _date_buckets(start: datetime, end: datetime, bucket_days: int) -> list[tuple[datetime, datetime]]:
    """Yield half-open [a, b) buckets, snapped to ``bucket_days`` from a fixed epoch.

    Snapping to a fixed epoch (here: 2000-01-01) keeps cache keys stable across
    different call ranges — fetching 2024-01 then 2024-02 hits the same cache
    shards as fetching all of 2024 in one shot.
    """
    epoch = datetime(2000, 1, 1, tzinfo=UTC)
    bucket = timedelta(days=bucket_days)
    s_days = (start - epoch).days
    bucket_start_days = (s_days // bucket_days) * bucket_days
    cur = epoch + timedelta(days=bucket_start_days)
    out: list[tuple[datetime, datetime]] = []
    while cur < end:
        nxt = cur + bucket
        out.append((max(cur, start), min(nxt, end)))
        cur = nxt
    return out


def fetch_archive(
    start: datetime,
    end: datetime,
    site: SiteSpec | None = None,
    variables: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Fetch hourly Open-Meteo archive for [start, end). Results are cached per 90-day bucket.

    Bucket files are immutable; once written they are read from disk on subsequent calls.
    """
    cfg = data_config()
    site = site or _site_from_config()
    variables = list(variables or cfg["openmeteo"]["variables"])
    bucket_days = int(cfg["openmeteo"]["bucket_days"])
    base = cfg["openmeteo"]["archive_endpoint"]
    cache = _cache_dir()

    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)

    frames: list[pd.DataFrame] = []
    for a, b in _date_buckets(start, end, bucket_days):
        params = {
            "latitude": site.latitude,
            "longitude": site.longitude,
            "start_date": a.date().isoformat(),
            "end_date": (b - timedelta(hours=1)).date().isoformat(),
            "hourly": ",".join(variables),
            "timezone": cfg["openmeteo"]["timezone"],
        }
        key = f"archive_{a.date()}_{b.date()}_{_params_hash(params)}.parquet"
        shard = cache / key
        if shard.exists():
            frames.append(pd.read_parquet(shard))
            continue
        payload = _request(base, params)
        df = _hourly_to_frame(payload, variables)
        df = df.loc[(df.index >= a) & (df.index < b)]
        df.to_parquet(shard)
        frames.append(df)

    if not frames:
        return pd.DataFrame(columns=list(variables))
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="first")]
    out = out.loc[(out.index >= start) & (out.index < end)]
    return out


def fetch_forecast(
    issue_time: datetime,
    horizon_hours: int,
    site: SiteSpec | None = None,
    variables: Iterable[str] | None = None,
    cache_ttl_seconds: int = 3600,
    past_hours: int = 0,
) -> pd.DataFrame:
    """Fetch a forecast covering [issue_time - past_hours, issue_time + horizon_hours).

    ``past_hours`` extends the window backwards using the forecast endpoint's
    recent-past coverage. This is how live inference obtains the hours between
    the ERA5 archive cut-off (~now-5d) and ``issue_time`` — the archive API
    cannot serve them, but the forecast endpoint can (up to 92 days back).
    """
    cfg = data_config()
    site = site or _site_from_config()
    variables = list(variables or cfg["openmeteo"]["variables"])
    base = cfg["openmeteo"]["forecast_endpoint"]
    cache = _cache_dir()

    issue_time = issue_time.astimezone(UTC) if issue_time.tzinfo else issue_time.replace(tzinfo=UTC)
    start = issue_time - timedelta(hours=past_hours)
    end = issue_time + timedelta(hours=horizon_hours)

    # Open-Meteo forecast takes whole UTC dates; we trim to [start, end) afterwards.
    params = {
        "latitude": site.latitude,
        "longitude": site.longitude,
        "start_date": start.date().isoformat(),
        "end_date": end.date().isoformat(),
        "hourly": ",".join(variables),
        "timezone": cfg["openmeteo"]["timezone"],
    }
    key = f"forecast_{issue_time:%Y%m%dT%H}_{past_hours}_{horizon_hours}_{_params_hash(params)}.parquet"
    shard = cache / key
    if shard.exists() and (time.time() - shard.stat().st_mtime) < cache_ttl_seconds:
        df = pd.read_parquet(shard)
    else:
        payload = _request(base, params)
        df = _hourly_to_frame(payload, variables)
        df.to_parquet(shard)

    df = df.loc[(df.index >= start) & (df.index < end)]
    return df
