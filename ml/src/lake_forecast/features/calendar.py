"""Calendar features: hour-of-day and day-of-year as sin/cos pairs."""

from __future__ import annotations

import numpy as np
import pandas as pd

from lake_forecast.config import features_config


def calendar_features(index: pd.DatetimeIndex) -> pd.DataFrame:
    cfg = features_config()["calendar"]
    hour_period = float(cfg["hour_period"])
    doy_period = float(cfg["doy_period"])

    if index.tz is None:
        raise ValueError("calendar_features requires a tz-aware DatetimeIndex (UTC).")

    hour = index.hour + index.minute / 60.0
    doy = index.dayofyear + (index.hour + index.minute / 60.0) / 24.0

    return pd.DataFrame(
        {
            "hour_sin": np.sin(2 * np.pi * hour / hour_period),
            "hour_cos": np.cos(2 * np.pi * hour / hour_period),
            "doy_sin": np.sin(2 * np.pi * doy / doy_period),
            "doy_cos": np.cos(2 * np.pi * doy / doy_period),
        },
        index=index,
    )
