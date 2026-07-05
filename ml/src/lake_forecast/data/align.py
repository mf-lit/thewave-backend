"""Left-join cleaned water-temp onto Open-Meteo archive weather.

This produces the master hourly table that feature engineering will read from.
Weather gaps of <= ``weather_ffill_limit`` are forward-filled; longer ones
leave NaNs that the training loop will mask out.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from lake_forecast.config import data_config, repo_path
from lake_forecast.data.clean import run_clean
from lake_forecast.io.openmeteo import fetch_archive


def join_weather(cleaned: pd.DataFrame, weather: pd.DataFrame, ffill_limit: int) -> pd.DataFrame:
    """Left-join hourly weather onto cleaned water-temp index."""
    weather = weather.sort_index()
    joined = cleaned.join(weather, how="left")
    weather_cols = list(weather.columns)
    joined[weather_cols] = joined[weather_cols].ffill(limit=ffill_limit)
    return joined


def build_master_table(
    cleaned_parquet: Path | str | None = None,
    weather_parquet: Path | str | None = None,
    out_parquet: Path | str | None = None,
) -> pd.DataFrame:
    cfg = data_config()
    cleaned_path = Path(cleaned_parquet) if cleaned_parquet else repo_path(cfg["paths"]["cleaned_parquet"])
    weather_path = (
        Path(weather_parquet) if weather_parquet else repo_path("data/interim/weather_archive.parquet")
    )
    out_path = Path(out_parquet) if out_parquet else repo_path(cfg["paths"]["weather_joined_parquet"])

    if not cleaned_path.exists():
        cleaned = run_clean()
    else:
        cleaned = pd.read_parquet(cleaned_path)

    if not weather_path.exists():
        from datetime import UTC, datetime, timedelta

        start = datetime.fromisoformat(cfg["date_range"]["history_start"]).replace(tzinfo=UTC)
        end = datetime.fromisoformat(cfg["date_range"]["history_end"]).replace(tzinfo=UTC) + timedelta(days=1)
        weather = fetch_archive(start, end)
        weather.to_parquet(weather_path)
    else:
        weather = pd.read_parquet(weather_path)

    ffill = int(cfg["cleaning"]["weather_ffill_limit"])
    joined = join_weather(cleaned, weather, ffill)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    joined.to_parquet(out_path)
    return joined


if __name__ == "__main__":  # pragma: no cover
    df = build_master_table()
    weather_cols = [c for c in df.columns if c not in ("water_temp", "water_stuck_mask")]
    print(f"rows={len(df)}  cols={len(df.columns)}")
    print(f"  water_temp non-null: {df['water_temp'].notna().sum()}")
    print(f"  weather any-null: {df[weather_cols].isna().any(axis=1).sum()}")
