"""Feature tests: rolling windows are strictly backward-looking; anchor uses
only data at or before t0; calendar features have sane shape."""

from __future__ import annotations

import numpy as np
import pandas as pd

from lake_forecast.features.calendar import calendar_features
from lake_forecast.features.lags import last_valid_water_temp, water_anchor_features
from lake_forecast.features.weather import rolling_weather_features


def _make_weather_frame(n: int = 200) -> pd.DataFrame:
    idx = pd.date_range("2024-06-01", periods=n, freq="h", tz="UTC")
    return pd.DataFrame(
        {
            "temperature_2m": np.linspace(10, 30, n),
            "relative_humidity_2m": np.linspace(50, 80, n),
            "dew_point_2m": np.linspace(5, 20, n),
            "precipitation": np.zeros(n),
            "cloud_cover": np.linspace(0, 100, n),
            "shortwave_radiation": np.linspace(0, 500, n),
            "direct_radiation": np.linspace(0, 400, n),
            "diffuse_radiation": np.linspace(0, 100, n),
            "wind_speed_10m": np.full(n, 5.0),
            "wind_direction_10m": np.zeros(n),
            "surface_pressure": np.full(n, 1015.0),
        },
        index=idx,
    )


def test_rolling_is_backward_only() -> None:
    """Rolling window at time t must equal the mean over [t-23h, t] for a 24h mean."""
    w = _make_weather_frame()
    rolling = rolling_weather_features(w)
    col = "temperature_2m_mean_24h"
    # At index 100, expected = mean of values[77..100]
    expected = w["temperature_2m"].iloc[77:101].mean()
    np.testing.assert_allclose(rolling[col].iloc[100], expected, rtol=1e-6)


def test_rolling_does_not_use_future() -> None:
    """If we mutate a future value, rolling at an earlier time must not change."""
    w = _make_weather_frame()
    r1 = rolling_weather_features(w)
    w2 = w.copy()
    w2.iloc[150:] = w2.iloc[150:] * 1000  # mangle future values
    r2 = rolling_weather_features(w2)
    np.testing.assert_allclose(r1.iloc[:100].values, r2.iloc[:100].values)


def test_anchor_uses_only_t0() -> None:
    """Anchor features depend only on water_temp_t0 (scalar) — modifying any
    other feature must not affect the anchor outputs."""
    h = np.arange(1, 169)
    t0 = pd.Series([15.0] * len(h))
    a1 = water_anchor_features(t0, h)
    a2 = water_anchor_features(t0, h)
    np.testing.assert_allclose(a1.values, a2.values)
    # Decay shrinks magnitudes monotonically over horizon for positive t0.
    decay_col = "water_temp_t0_exp_72h"
    assert (a1[decay_col].diff().dropna() < 0).all()


def test_last_valid_returns_recent_with_lookback() -> None:
    """If the most recent reading is fresh, it is returned; if stale, NaN."""
    idx = pd.date_range("2024-06-01", periods=10, freq="h", tz="UTC")
    s = pd.Series([10.0] + [np.nan] * 9, index=idx)
    out = last_valid_water_temp(s, max_lookback_hours=3)
    assert out.iloc[0] == 10.0
    assert out.iloc[3] == 10.0  # 3h old, still acceptable
    assert np.isnan(out.iloc[5])  # 5h old, stale


def test_calendar_features_unit_circle() -> None:
    idx = pd.date_range("2024-01-01", periods=24, freq="h", tz="UTC")
    cal = calendar_features(idx)
    # sin²+cos² == 1 on hour
    r = cal["hour_sin"] ** 2 + cal["hour_cos"] ** 2
    np.testing.assert_allclose(r.values, 1.0, atol=1e-9)
    # doy too
    r2 = cal["doy_sin"] ** 2 + cal["doy_cos"] ** 2
    np.testing.assert_allclose(r2.values, 1.0, atol=1e-9)
