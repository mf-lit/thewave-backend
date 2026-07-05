"""Build the per-(issue_time, horizon) feature matrix.

This module is the single source of truth for the feature schema. It is used
by:
  * training (over historical archive weather)
  * inference (over a freshly fetched 168-hour forecast window)

A "sample" is one (issue_time, horizon) pair. The feature columns are:
  * weather at the **target** time ``t = issue_time + h`` (instantaneous and
    rolling, computed over the contiguous weather time series so rolling
    windows are well-defined at every ``t``)
  * calendar at the target time
  * water-anchor features (``water_temp_t0`` known at issue time + decay)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from lake_forecast.config import data_config, repo_path, train_config
from lake_forecast.features.calendar import calendar_features
from lake_forecast.features.lags import last_valid_water_temp, water_anchor_features
from lake_forecast.features.weather import rolling_weather_features

WEATHER_VARS: list[str] = list(data_config()["openmeteo"]["variables"])


@dataclass
class FeatureMatrix:
    X: pd.DataFrame  # one row per (issue_time, horizon); indexed by target time
    y: pd.Series  # target water_temp at target time (NaN where unavailable)
    issue_time: pd.Series  # issue_time per row (UTC)
    horizon: pd.Series  # int horizon hours per row
    target_time: pd.Series  # target time per row (UTC)
    feature_columns: list[str]


def _instant_weather(weather: pd.DataFrame, target_times: pd.DatetimeIndex) -> pd.DataFrame:
    return weather.reindex(target_times).rename(columns={c: f"wx_{c}" for c in weather.columns})


def _rolling_at_target(rolling: pd.DataFrame, target_times: pd.DatetimeIndex) -> pd.DataFrame:
    return rolling.reindex(target_times)


def _calendar_at_target(target_times: pd.DatetimeIndex) -> pd.DataFrame:
    return calendar_features(target_times).reset_index(drop=True).set_index(target_times)


def build_training_matrix(
    master: pd.DataFrame,
    horizon_hours: int | None = None,
    issue_stride_hours: int | None = None,
    issue_time_start: pd.Timestamp | None = None,
    issue_time_end: pd.Timestamp | None = None,
) -> FeatureMatrix:
    """Build a long-form (issue_time x horizon) feature matrix from the master table.

    ``master`` must contain hourly UTC water_temp + all weather variables on a
    contiguous index.
    """
    tcfg = train_config()
    horizon_hours = int(horizon_hours or tcfg["forecast"]["horizon_hours"])
    issue_stride = int(issue_stride_hours or tcfg["forecast"]["issue_stride_hours"])

    weather = master[WEATHER_VARS].copy()
    water_temp = master["water_temp"].copy()

    # Pre-compute rolling weather and anchor over the whole time-series.
    rolling = rolling_weather_features(weather)
    anchor_t0 = last_valid_water_temp(water_temp, max_lookback_hours=24)

    # Choose issue times on the stride; restrict to range where t0 is known and
    # the full horizon fits within the master index.
    full_idx = master.index
    candidate_issue = full_idx[::issue_stride]
    horizon_end = full_idx.max() - pd.Timedelta(hours=horizon_hours - 1)
    candidate_issue = candidate_issue[candidate_issue <= horizon_end]
    if issue_time_start is not None:
        candidate_issue = candidate_issue[candidate_issue >= issue_time_start]
    if issue_time_end is not None:
        candidate_issue = candidate_issue[candidate_issue <= issue_time_end]

    # Drop issue times with no fresh water-temp anchor.
    anchor_at_issue = anchor_t0.reindex(candidate_issue)
    valid_issue = candidate_issue[anchor_at_issue.notna()]
    if len(valid_issue) == 0:
        raise ValueError("No valid issue times with a fresh water-temp anchor.")

    # Cartesian product (issue_time × horizon).
    horizons = np.arange(1, horizon_hours + 1, dtype=np.int32)
    issue_arr = np.repeat(valid_issue.values, horizons.size)
    h_arr = np.tile(horizons, valid_issue.size)
    issue_series = pd.DatetimeIndex(issue_arr, tz="UTC")
    target_series = issue_series + pd.to_timedelta(h_arr, unit="h")

    # Drop rows whose target time exceeds the master horizon (boundary defensive).
    in_range = target_series <= full_idx.max()
    issue_series = issue_series[in_range]
    target_series = target_series[in_range]
    h_arr = h_arr[in_range]

    instant = _instant_weather(weather, target_series).reset_index(drop=True)
    roll_t = _rolling_at_target(rolling, target_series).reset_index(drop=True)
    cal = calendar_features(target_series).reset_index(drop=True)

    t0_values = anchor_t0.reindex(issue_series).values
    anchor = water_anchor_features(
        pd.Series(t0_values, index=pd.RangeIndex(len(issue_series))),
        h_arr,
    )

    X = pd.concat([instant, roll_t, cal, anchor], axis=1)
    y_arr = water_temp.reindex(target_series).values
    y = pd.Series(y_arr, name="water_temp_target")

    fm = FeatureMatrix(
        X=X,
        y=y,
        issue_time=pd.Series(issue_series.values, name="issue_time"),
        horizon=pd.Series(h_arr.astype(np.int32), name="horizon_h"),
        target_time=pd.Series(target_series.values, name="target_time"),
        feature_columns=list(X.columns),
    )
    return fm


def build_inference_matrix(
    issue_time: pd.Timestamp,
    water_temp_t0: float,
    forecast_weather: pd.DataFrame,
    historical_weather_tail: pd.DataFrame,
    horizon_hours: int | None = None,
) -> FeatureMatrix:
    """Construct a single-issue 168-row feature matrix for inference.

    ``forecast_weather`` is hourly Open-Meteo forecast for [issue_time, issue_time + h].
    ``historical_weather_tail`` is hourly archive weather covering at least the
    longest rolling window leading up to ``issue_time`` (typically 168h tail).
    """
    tcfg = train_config()
    horizon_hours = int(horizon_hours or tcfg["forecast"]["horizon_hours"])

    if issue_time.tzinfo is None:
        issue_time = issue_time.tz_localize("UTC")
    else:
        issue_time = issue_time.tz_convert("UTC")

    # Concatenate past + forecast for rolling-window computation.
    weather = pd.concat([historical_weather_tail, forecast_weather]).sort_index()
    weather = weather[~weather.index.duplicated(keep="last")]
    weather = weather[WEATHER_VARS]

    horizons = np.arange(1, horizon_hours + 1, dtype=np.int32)
    target_times = pd.DatetimeIndex([issue_time + pd.Timedelta(hours=int(h)) for h in horizons])

    rolling = rolling_weather_features(weather)
    instant = _instant_weather(weather, target_times).reset_index(drop=True)
    roll_t = _rolling_at_target(rolling, target_times).reset_index(drop=True)
    cal = calendar_features(target_times).reset_index(drop=True)
    anchor = water_anchor_features(
        pd.Series([water_temp_t0] * horizons.size, index=pd.RangeIndex(horizons.size)),
        horizons,
    )

    X = pd.concat([instant, roll_t, cal, anchor], axis=1)
    y = pd.Series([np.nan] * horizons.size, name="water_temp_target")
    return FeatureMatrix(
        X=X,
        y=y,
        issue_time=pd.Series([issue_time] * horizons.size, name="issue_time"),
        horizon=pd.Series(horizons, name="horizon_h"),
        target_time=pd.Series(target_times.values, name="target_time"),
        feature_columns=list(X.columns),
    )


def save_feature_matrix(fm: FeatureMatrix, path: str) -> None:
    out = repo_path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    bundle = pd.concat(
        [
            fm.X.reset_index(drop=True),
            fm.y.reset_index(drop=True),
            fm.issue_time.reset_index(drop=True),
            fm.horizon.reset_index(drop=True),
            fm.target_time.reset_index(drop=True),
        ],
        axis=1,
    )
    bundle.to_parquet(out)


def load_feature_matrix(path: str) -> FeatureMatrix:
    bundle = pd.read_parquet(repo_path(path))
    meta_cols = ["water_temp_target", "issue_time", "horizon_h", "target_time"]
    feature_cols = [c for c in bundle.columns if c not in meta_cols]
    return FeatureMatrix(
        X=bundle[feature_cols],
        y=bundle["water_temp_target"],
        issue_time=bundle["issue_time"],
        horizon=bundle["horizon_h"],
        target_time=bundle["target_time"],
        feature_columns=feature_cols,
    )
