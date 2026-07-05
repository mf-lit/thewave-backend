"""Eval tests: daytime mask is in local time and DST-aware; MAE plumbing."""

from __future__ import annotations

import numpy as np
import pandas as pd

from lake_forecast.eval import daytime_mask, evaluate


def test_daytime_mask_local_hours() -> None:
    # 06:00 local on a non-DST date (Jan)
    t_in = pd.Timestamp("2025-01-15T06:00", tz="Europe/London").tz_convert("UTC")
    t_out_morning = pd.Timestamp("2025-01-15T05:00", tz="Europe/London").tz_convert("UTC")
    t_out_evening = pd.Timestamp("2025-01-15T22:00", tz="Europe/London").tz_convert("UTC")
    s = pd.Series([t_in, t_out_morning, t_out_evening])
    m = daytime_mask(s)
    assert m.tolist() == [True, False, False]


def test_daytime_mask_handles_dst() -> None:
    # 06:00 local in mid-July (BST = UTC+1)
    t_local = pd.Timestamp("2025-07-15T06:00", tz="Europe/London")
    t_utc = t_local.tz_convert("UTC")  # 05:00 UTC
    m = daytime_mask(pd.Series([t_utc]))
    assert m[0] == True  # noqa: E712


def test_evaluate_mae_only_uses_daytime() -> None:
    rng = pd.date_range("2025-06-01", periods=24, freq="h", tz="UTC")
    # Predict 0; truth = 1 except night hours (UTC ~ local hour - 1 in summer)
    y_true = np.ones(24)
    y_pred = np.zeros(24)
    horizon = np.arange(1, 25)
    res = evaluate(y_true=y_true, y_pred=y_pred, horizon=horizon, target_time=pd.Series(rng))
    # MAE on the daytime rows is 1.0 (all pred=0, true=1)
    assert abs(res.mae - 1.0) < 1e-9
    # Only daytime rows counted
    assert res.n < 24
