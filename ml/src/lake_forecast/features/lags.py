"""Water-temp anchor features.

Per plan: at inference time we only know the most-recent observation, so the
training-time feature set must reflect that. For an issue time ``t0`` and a
target horizon ``h`` we expose:
  * ``water_temp_t0`` — most recent valid water-temp at or before ``t0``
  * ``hours_since_t0`` — equals ``h``
  * ``water_temp_t0_exp_{tau}h`` — ``water_temp_t0 * exp(-h / tau)`` for each
    decay constant.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from lake_forecast.config import features_config


def water_anchor_features(
    water_temp_t0: pd.Series,
    horizon_hours: pd.Series | np.ndarray,
) -> pd.DataFrame:
    """Build anchor features. Inputs must be aligned by row.

    ``water_temp_t0`` may be NaN — downstream training will drop those rows.
    """
    cfg = features_config()["water_anchor"]
    taus = [int(t) for t in cfg["decay_taus_hours"]]

    h = np.asarray(horizon_hours, dtype=float)
    t0_arr = np.asarray(water_temp_t0, dtype=float)

    out = {
        "water_temp_t0": t0_arr,
        "hours_since_t0": h,
    }
    for tau in taus:
        out[f"water_temp_t0_exp_{tau}h"] = t0_arr * np.exp(-h / float(tau))

    return pd.DataFrame(out, index=water_temp_t0.index)


def last_valid_water_temp(water_temp: pd.Series, max_lookback_hours: int = 24) -> pd.Series:
    """At each timestamp ``t``, return the most recent valid water_temp seen at or before ``t``.

    If the most recent valid reading is older than ``max_lookback_hours`` it is
    returned as NaN — this lets the trainer drop issue times where no fresh
    anchor exists (mirrors the inference-time staleness check).

    Assumes ``water_temp`` is on a contiguous hourly grid (position-delta ==
    hour-delta). That invariant is enforced in :func:`data.clean.clean_water_temp`.
    """
    s = water_temp.ffill()
    is_valid = water_temp.notna().to_numpy()
    # "hours since last valid" = cumulative count of consecutive non-valid rows,
    # zeroed at every valid row.
    hours_since = np.zeros(len(water_temp), dtype=np.int64)
    counter = 0
    sentinel = np.iinfo(np.int64).max
    for i in range(len(water_temp)):
        if is_valid[i]:
            counter = 0
        else:
            counter = counter + 1 if counter != sentinel else sentinel
        hours_since[i] = counter
    # Before the first valid reading: leave NaN.
    if is_valid.any():
        first_valid = int(np.argmax(is_valid))
    else:
        first_valid = len(water_temp)
    mask_pre_first = np.arange(len(water_temp)) < first_valid

    out = s.where(pd.Series(hours_since <= max_lookback_hours, index=water_temp.index))
    if mask_pre_first.any():
        out = out.where(~pd.Series(mask_pre_first, index=water_temp.index))
    return out
