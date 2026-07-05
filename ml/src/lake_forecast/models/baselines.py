"""Simple baselines: persistence, seasonal-naive, air≈water."""

from __future__ import annotations

import numpy as np
import pandas as pd

from lake_forecast.models.base import Forecaster


class PersistenceModel(Forecaster):
    """Predict water_temp_t0 for every horizon."""

    name = "persistence"

    def fit(self, X: pd.DataFrame, y: np.ndarray, *, sample_weight=None, eval_set=None) -> PersistenceModel:
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if "water_temp_t0" not in X.columns:
            raise KeyError("PersistenceModel requires 'water_temp_t0' feature.")
        return X["water_temp_t0"].to_numpy(dtype=float)


class AirEqualsWaterModel(Forecaster):
    """Predict temperature_2m at the target hour."""

    name = "air_equals_water"

    def fit(self, X: pd.DataFrame, y: np.ndarray, *, sample_weight=None, eval_set=None) -> AirEqualsWaterModel:
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if "wx_temperature_2m" not in X.columns:
            raise KeyError("AirEqualsWaterModel requires 'wx_temperature_2m'.")
        return X["wx_temperature_2m"].to_numpy(dtype=float)


class SeasonalNaiveModel(Forecaster):
    """Predict mean water_temp by (month, hour-of-day) from training data.

    Indexed by local-time month/hour (Europe/London).
    """

    name = "seasonal_naive"

    def __init__(self, local_tz: str = "Europe/London") -> None:
        self.local_tz = local_tz
        self.table: pd.Series | None = None
        self.global_mean: float = float("nan")

    def fit(
        self,
        X: pd.DataFrame,
        y: np.ndarray,
        *,
        sample_weight=None,
        eval_set=None,
        target_time: pd.Series | None = None,
    ) -> SeasonalNaiveModel:
        if target_time is None:
            raise ValueError("SeasonalNaiveModel.fit requires target_time")
        idx = pd.DatetimeIndex(pd.to_datetime(target_time, utc=True)).tz_convert(self.local_tz)
        df = pd.DataFrame({"y": y, "month": idx.month, "hour": idx.hour}).dropna(subset=["y"])
        self.table = df.groupby(["month", "hour"])["y"].mean()
        self.global_mean = float(df["y"].mean())
        return self

    def predict(self, X: pd.DataFrame, target_time: pd.Series | None = None) -> np.ndarray:
        if self.table is None:
            raise RuntimeError("SeasonalNaiveModel not fit yet")
        if target_time is None:
            raise ValueError("SeasonalNaiveModel.predict requires target_time")
        idx = pd.DatetimeIndex(pd.to_datetime(target_time, utc=True)).tz_convert(self.local_tz)
        keys = list(zip(idx.month, idx.hour))
        out = np.array([self.table.get(k, self.global_mean) for k in keys], dtype=float)
        return out
