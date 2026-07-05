"""Daytime-masked evaluation: MAE on local-time hours 06:00–21:59.

Local time is Europe/London (DST-aware via zoneinfo). The mask is applied to
both training sample weights and metric computation so models don't expend
capacity on night hours we never report.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from lake_forecast.config import train_config


def daytime_mask(target_time: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    """Return a boolean mask True for rows whose local-time hour ∈ [hour_start, hour_end_exclusive)."""
    cfg = train_config()["daytime_mask"]
    tz = str(cfg["local_tz"])
    h_lo = int(cfg["hour_start"])
    h_hi = int(cfg["hour_end_exclusive"])

    idx = pd.DatetimeIndex(pd.to_datetime(target_time, utc=True))
    local = idx.tz_convert(tz)
    h = local.hour.values
    return (h >= h_lo) & (h < h_hi)


@dataclass
class EvalResult:
    mae: float
    n: int
    per_horizon: pd.DataFrame
    per_month: pd.DataFrame
    skill_vs_persistence: float | None = None


def _per_horizon_mae(err: np.ndarray, horizon: np.ndarray, daytime: np.ndarray) -> pd.DataFrame:
    df = pd.DataFrame({"err": np.abs(err), "h": horizon, "day": daytime}).query("day")
    g = df.groupby("h")["err"].agg(["mean", "count"]).reset_index()
    g.columns = ["horizon_h", "mae", "n"]
    return g


def _per_month_mae(err: np.ndarray, target_time: pd.Series, daytime: np.ndarray) -> pd.DataFrame:
    local = pd.to_datetime(target_time, utc=True).dt.tz_convert("Europe/London")
    df = pd.DataFrame(
        {
            "err": np.abs(err),
            "month": local.dt.strftime("%Y-%m"),
            "day": daytime,
        }
    ).query("day")
    g = df.groupby("month")["err"].agg(["mean", "count"]).reset_index()
    g.columns = ["month", "mae", "n"]
    return g


def evaluate(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    horizon: np.ndarray,
    target_time: pd.Series,
    persistence_pred: np.ndarray | None = None,
) -> EvalResult:
    """Compute masked MAE, per-horizon and per-month breakdowns, plus skill score."""
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    horizon = np.asarray(horizon, dtype=int)

    day = daytime_mask(target_time)
    valid = day & np.isfinite(y_true) & np.isfinite(y_pred)

    err = y_pred - y_true
    mae = float(np.mean(np.abs(err[valid]))) if valid.any() else float("nan")

    skill = None
    if persistence_pred is not None:
        pp = np.asarray(persistence_pred, dtype=float)
        v2 = valid & np.isfinite(pp)
        if v2.any():
            mae_p = float(np.mean(np.abs(pp[v2] - y_true[v2])))
            if mae_p > 0:
                skill = 1.0 - mae / mae_p

    return EvalResult(
        mae=mae,
        n=int(valid.sum()),
        per_horizon=_per_horizon_mae(err, horizon, valid),
        per_month=_per_month_mae(err, target_time, valid),
        skill_vs_persistence=skill,
    )
