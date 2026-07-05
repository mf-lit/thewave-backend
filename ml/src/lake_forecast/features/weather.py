"""Backward-looking rolling weather aggregates.

Every window here is strictly backward-looking (closed='left' would exclude
the current hour; we use closed='right' so window includes ``t`` itself but
NOT any future time — features are computed from values up to and including ``t``
which is safe because weather at ``t`` is known from the forecast at training
and inference time alike).

Used both for instantaneous-weather features at target hour ``t+h`` and for
windows over the contiguous historical/forecast weather time series, since the
same module is reused at inference.
"""

from __future__ import annotations

import pandas as pd

from lake_forecast.config import features_config


def rolling_weather_features(weather: pd.DataFrame) -> pd.DataFrame:
    """Compute rolling weather aggregates per features.yaml.

    Returns a DataFrame indexed identically to ``weather`` with columns named
    ``{var}_{agg}_{window}h``.
    """
    cfg = features_config()["weather_rolling"]
    out: dict[str, pd.Series] = {}

    def _roll(series: pd.Series, window: int, agg: str) -> pd.Series:
        roller = series.rolling(window=f"{window}h", min_periods=1, closed="right")
        if agg == "mean":
            return roller.mean()
        if agg == "sum":
            return roller.sum()
        raise ValueError(f"Unknown agg {agg!r}")

    aggs: dict[str, str] = {
        "temperature_2m": "mean",
        "shortwave_radiation": "sum",
        "precipitation": "sum",
        "wind_speed_10m": "mean",
        "cloud_cover": "mean",
    }

    for var, windows in cfg.items():
        if var not in weather.columns:
            raise KeyError(f"Variable {var!r} missing from weather frame.")
        agg = aggs[var]
        for w in windows:
            name = f"{var}_{agg}_{int(w)}h"
            out[name] = _roll(weather[var], int(w), agg)

    return pd.DataFrame(out, index=weather.index)
