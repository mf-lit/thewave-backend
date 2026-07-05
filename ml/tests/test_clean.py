"""Cleaning tests: stuck-sensor masking, reindex, dedup, NaN handling."""

from __future__ import annotations

import numpy as np
import pandas as pd

from lake_forecast.data.clean import clean_water_temp


def _times(n: int) -> list[str]:
    base = pd.Timestamp("2024-01-01T00:00:00Z")
    return [(base + pd.Timedelta(hours=i)).isoformat() for i in range(n)]


def test_floor_and_dedup() -> None:
    df = pd.DataFrame(
        {
            "time": [
                "2024-01-01T00:03:00Z",
                "2024-01-01T00:55:00Z",
                "2024-01-01T01:00:00Z",
            ],
            "water_temp": [10.0, 10.5, 11.0],
        }
    )
    out = clean_water_temp(df, stuck_run_min_length=6, water_temp_clip=(0.0, 30.0))
    assert len(out) == 2
    assert out.iloc[0]["water_temp"] == 10.0  # first reading kept after floor+dedup
    assert out.iloc[1]["water_temp"] == 11.0


def test_stuck_sensor_masks_only_after_first() -> None:
    t = _times(10)
    vals = [12.0, 12.0, 12.0, 12.0, 12.0, 12.0, 12.0, 13.0, 14.0, 15.0]
    df = pd.DataFrame({"time": t, "water_temp": vals})
    out = clean_water_temp(df, stuck_run_min_length=6, water_temp_clip=(0.0, 30.0))
    assert out.iloc[0]["water_temp"] == 12.0  # first kept
    assert np.isnan(out.iloc[1:7]["water_temp"]).all()
    assert out["water_stuck_mask"].iloc[0] is np.False_ or not bool(out["water_stuck_mask"].iloc[0])
    assert out["water_stuck_mask"].iloc[1:7].all()
    # values after the stuck run remain
    assert out.iloc[7]["water_temp"] == 13.0


def test_stuck_below_threshold_unchanged() -> None:
    t = _times(8)
    vals = [12.0, 12.0, 12.0, 12.0, 12.0, 13.0, 14.0, 15.0]  # run of 5
    df = pd.DataFrame({"time": t, "water_temp": vals})
    out = clean_water_temp(df, stuck_run_min_length=6, water_temp_clip=(0.0, 30.0))
    # No row should be masked
    assert not out["water_stuck_mask"].any()
    assert out["water_temp"].notna().sum() == 8


def test_stuck_run_spans_nan_gap() -> None:
    # A frozen sensor with a one-hour dropout must still be detected as a single
    # stuck run. Otherwise the gap manufactures a fresh "first of run" reading,
    # which becomes a recent `last valid` anchor and defeats the 24h staleness
    # guard — letting forecasts keep running off a dead sensor indefinitely.
    base = pd.Timestamp("2024-01-01T00:00:00Z")
    hours = [0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11]  # hour 5 dropped
    df = pd.DataFrame(
        {
            "time": [(base + pd.Timedelta(hours=h)).isoformat() for h in hours],
            "water_temp": [20.1] * len(hours),
        }
    )
    out = clean_water_temp(df, stuck_run_min_length=6, water_temp_clip=(0.0, 30.0))
    assert len(out) == 12  # 00..11 reindexed onto a contiguous grid
    # Only the very first reading survives; the run is not reset by the gap.
    assert out.iloc[0]["water_temp"] == 20.1
    assert out["water_temp"].notna().sum() == 1
    assert out["water_temp"].last_valid_index() == base
    # The genuine missing hour is NaN but not flagged as stuck.
    assert np.isnan(out.iloc[5]["water_temp"])
    assert not bool(out["water_stuck_mask"].iloc[5])
    # A duplicate reading on the far side of the gap is still flagged.
    assert bool(out["water_stuck_mask"].iloc[6])


def test_changed_value_across_gap_is_not_one_run() -> None:
    # Only *identical* values bridge a gap. A different reading after the gap
    # starts a fresh run, so neither side reaches the threshold and nothing is
    # masked.
    base = pd.Timestamp("2024-01-01T00:00:00Z")
    hours = [0, 1, 2, 3, 5, 6, 7, 8]  # hour 4 dropped
    vals = [12.0, 12.0, 12.0, 12.0, 13.0, 13.0, 13.0, 13.0]
    df = pd.DataFrame(
        {
            "time": [(base + pd.Timedelta(hours=h)).isoformat() for h in hours],
            "water_temp": vals,
        }
    )
    out = clean_water_temp(df, stuck_run_min_length=6, water_temp_clip=(0.0, 30.0))
    assert not out["water_stuck_mask"].any()
    assert out["water_temp"].notna().sum() == 8


def test_reindex_fills_gaps_with_nan() -> None:
    df = pd.DataFrame(
        {
            "time": ["2024-01-01T00:00:00Z", "2024-01-01T05:00:00Z"],
            "water_temp": [10.0, 12.0],
        }
    )
    out = clean_water_temp(df, stuck_run_min_length=6, water_temp_clip=(0.0, 30.0))
    assert len(out) == 6  # 00..05 inclusive hourly
    assert out.iloc[0]["water_temp"] == 10.0
    assert np.isnan(out.iloc[1:5]["water_temp"]).all()
    assert out.iloc[5]["water_temp"] == 12.0


def test_clip_drops_out_of_range() -> None:
    t = _times(3)
    df = pd.DataFrame({"time": t, "water_temp": [-5.0, 15.0, 200.0]})
    out = clean_water_temp(df, stuck_run_min_length=6, water_temp_clip=(0.0, 30.0))
    assert np.isnan(out.iloc[0]["water_temp"])  # -5 clipped to NaN
    assert out.iloc[1]["water_temp"] == 15.0
    assert np.isnan(out.iloc[2]["water_temp"])  # 200 clipped


def test_air_temp_dropped() -> None:
    t = _times(3)
    df = pd.DataFrame({"time": t, "water_temp": [10.0, 11.0, 12.0], "air_temp": [9, 10, 11]})
    out = clean_water_temp(df, stuck_run_min_length=6, water_temp_clip=(0.0, 30.0))
    assert "air_temp" not in out.columns
