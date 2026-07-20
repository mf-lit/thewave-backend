"""Clean raw water-temperature CSV onto a contiguous hourly grid.

Responsibilities (per plan):
  * Parse `time` and floor to the hour.
  * Drop duplicate hours (keep first).
  * Reindex onto a complete hourly grid spanning min..max time, leaving NaNs
    in physically-missing hours (sensor was offline for long stretches).
  * Apply the stuck-sensor mask: any run of identical `water_temp` of length
    >= ``stuck_run_min_length`` keeps only its first hour and NaNs the rest.
    Runs are detected over finite readings only, skipping NaN gaps, so a dropped
    hour cannot split one frozen run into two. The row-level mask is exposed as
    ``water_stuck_mask``.
  * Sanity-clip remaining values to ``water_temp_clip``.
  * Drop the rounded CSV ``air_temp`` column; Open-Meteo ``temperature_2m``
    will be joined later as the higher-resolution replacement.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from lake_forecast.config import data_config, repo_path


def _stuck_mask(values: np.ndarray, min_length: int) -> np.ndarray:
    """Boolean stuck-sensor mask, tolerant of NaN gaps in a frozen run.

    A "run" is a maximal sequence of *finite* readings that share one value,
    formed by skipping over any NaN gaps between them — so a missing hour does
    NOT reset the run. This matters because the source grid is reindexed onto a
    contiguous hourly axis: a single dropped reading splits a long frozen run
    into two, and each fragment's first hour survives as a fresh ``last valid``
    anchor. That defeats the downstream staleness guard, which would otherwise
    halt forecasting once the genuinely-changing readings are >24h old.

    Within a run of length >= ``min_length`` every reading except the first is
    flagged. NaN positions are never flagged (they are missing, not stuck).
    """
    n = values.shape[0]
    mask = np.zeros(n, dtype=bool)
    fin_idx = np.flatnonzero(np.isfinite(values))
    if fin_idx.size == 0:
        return mask
    # Work in "finite-only" space so NaN gaps are invisible to run detection.
    fin_vals = values[fin_idx]
    same_as_prev = np.zeros(fin_idx.size, dtype=bool)
    same_as_prev[1:] = fin_vals[1:] == fin_vals[:-1]
    # group id increments where the equality chain breaks
    group_id = np.cumsum(~same_as_prev)
    run_len = np.bincount(group_id)[group_id]
    mask[fin_idx] = (run_len >= min_length) & same_as_prev
    return mask


def clean_water_temp(
    df: pd.DataFrame,
    stuck_run_min_length: int,
    water_temp_clip: tuple[float, float],
) -> pd.DataFrame:
    """Apply cleaning rules to a raw CSV-shaped DataFrame.

    Input must have columns ``time``, ``water_temp``, optionally ``air_temp``.
    Returns a DataFrame indexed by hourly UTC timestamps with columns
    ``water_temp`` (float, NaN where missing or masked) and
    ``water_stuck_mask`` (bool, True where the stuck-sensor rule masked).
    """
    out = df.copy()
    out["time"] = pd.to_datetime(out["time"], utc=True, errors="coerce").dt.floor("h")
    out = out.dropna(subset=["time"]).drop_duplicates(subset=["time"], keep="first")
    out = out.set_index("time").sort_index()

    if out.empty:
        raise ValueError("No valid timestamps in raw data after parsing.")

    full_idx = pd.date_range(out.index.min(), out.index.max(), freq="h", tz="UTC")
    out = out.reindex(full_idx)
    out.index.name = "time"

    # Drop the rounded CSV air_temp; Open-Meteo temperature_2m will replace it.
    if "air_temp" in out.columns:
        out = out.drop(columns=["air_temp"])

    values = out["water_temp"].to_numpy(dtype=float)
    # A row is masked if it sits in a long (gap-tolerant) run of identical
    # readings AND is not the first reading of that run.
    stuck_mask = _stuck_mask(values, stuck_run_min_length)

    masked_values = values.copy()
    masked_values[stuck_mask] = np.nan

    lo, hi = water_temp_clip
    finite = np.isfinite(masked_values)
    outside = finite & ((masked_values < lo) | (masked_values > hi))
    masked_values[outside] = np.nan

    out["water_temp"] = masked_values
    out["water_stuck_mask"] = stuck_mask
    return out


def run_clean(raw_csv: Path | str | None = None, out_parquet: Path | str | None = None) -> pd.DataFrame:
    cfg = data_config()
    raw_csv = Path(raw_csv) if raw_csv else repo_path(cfg["paths"]["raw_csv"])
    out_parquet = Path(out_parquet) if out_parquet else repo_path(cfg["paths"]["cleaned_parquet"])

    # The source CSV's first three columns are time, water_temp, air_temp; any
    # further columns (e.g. forecast fields or free-text notes, which can be
    # ragged/unquoted) are ignored. air_temp is dropped downstream. Reading just
    # the leading columns tolerates a ragged tail rather than dropping valid rows.
    raw = pd.read_csv(
        raw_csv,
        usecols=[0, 1, 2],
        names=["time", "water_temp", "air_temp"],
        header=0,
        engine="python",
        on_bad_lines="skip",
    )
    cleaned = clean_water_temp(
        raw,
        stuck_run_min_length=int(cfg["cleaning"]["stuck_run_min_length"]),
        water_temp_clip=tuple(cfg["cleaning"]["water_temp_clip"]),  # type: ignore[arg-type]
    )

    out_parquet.parent.mkdir(parents=True, exist_ok=True)
    cleaned.to_parquet(out_parquet)
    return cleaned


if __name__ == "__main__":  # pragma: no cover
    cleaned = run_clean()
    valid = cleaned["water_temp"].notna().sum()
    stuck = cleaned["water_stuck_mask"].sum()
    print(
        f"cleaned rows={len(cleaned)} valid={valid} stuck_masked={stuck} "
        f"range=[{cleaned.index.min()}..{cleaned.index.max()}]"
    )
