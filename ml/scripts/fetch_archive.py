"""One-shot historical Open-Meteo pull. Idempotent; uses parquet shard cache."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from lake_forecast.config import data_config, repo_path
from lake_forecast.io.openmeteo import fetch_archive


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def main() -> None:
    cfg = data_config()
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=cfg["date_range"]["history_start"])
    parser.add_argument("--end", default=cfg["date_range"]["history_end"])
    args = parser.parse_args()

    start = _parse_date(args.start)
    # Archive end is exclusive; bump by one day to cover the final date inclusively.
    end = _parse_date(args.end) + timedelta(days=1)

    print(f"Fetching Open-Meteo archive {start.date()} → {end.date()} (exclusive)…")
    df = fetch_archive(start, end)
    out = repo_path("data/interim/weather_archive.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out)
    print(f"  wrote {len(df)} rows → {out}")
    print(f"  variables: {list(df.columns)}")
    print(f"  range: {df.index.min()} .. {df.index.max()}")


if __name__ == "__main__":
    main()
