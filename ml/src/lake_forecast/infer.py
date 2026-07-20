"""168-hour forecast pipeline.

1. Resolve issue_time (default = now floor 1h, UTC).
2. Load state.json (last_water_temp, last_obs_time). Refuse if stale.
3. Fetch Open-Meteo forecast for the next 168h and a tail of archive weather
   (long enough to populate rolling windows).
4. Build the inference feature matrix via the same module used at training.
5. Load best artifact under models/best/ and predict.

The "best" model can be either a sklearn-style joblib pickle (Huber/LightGBM)
or a PyTorch GRU checkpoint. ``metadata.json`` tells us which one.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from lake_forecast.config import data_config, repo_path, train_config
from lake_forecast.eval import daytime_mask
from lake_forecast.features.build import build_inference_matrix
from lake_forecast.io.openmeteo import fetch_forecast

# Hours of recent weather to pull ahead of issue_time. The GRU encoder spans the
# last `history_hours` (72h) and each of those rows carries a 168h backward
# rolling window, so we need ~72+168h of real history for full (non-truncated)
# rolling aggregates at every encoder row.
WEATHER_TAIL_HOURS = 252


def update_state_file(
    observed: float,
    state_path: Path,
    now: datetime | None = None,
    obs_time: datetime | None = None,
) -> Path:
    """Persist the latest water_temp anchor to ``state_path``.

    ``obs_time`` is when the reading was actually taken (defaults to now). It is
    what the staleness guard in :func:`_read_state` checks against, so passing
    the true observation time keeps that guard meaningful.
    """
    stamp = obs_time or now or datetime.now(UTC)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_water_temp": float(observed),
        "last_obs_time": stamp.replace(microsecond=0).isoformat(),
    }
    state_path.write_text(json.dumps(payload, indent=2))
    return state_path


def refresh_state_from_source(state_path: Path) -> dict[str, Any]:
    """Refresh the anchor from the live source CSV (configs/data.yaml paths.raw_csv).

    Reads through the same cleaning pipeline used for training so the anchor is
    the most recent reading that survives the stuck-sensor mask, then writes it
    to ``state_path`` stamped with that reading's own timestamp.
    """
    from lake_forecast.data.clean import run_clean

    cleaned = run_clean()
    water = cleaned["water_temp"]
    last_idx = water.last_valid_index()
    if last_idx is None:
        raise RuntimeError("no valid water_temp readings in the source CSV")
    obs_time = pd.Timestamp(last_idx).to_pydatetime()
    value = float(water.loc[last_idx])
    update_state_file(observed=value, state_path=state_path, obs_time=obs_time)
    return {"last_water_temp": value, "last_obs_time": obs_time.isoformat()}


def write_forecast_csv(
    df: pd.DataFrame,
    issue_time: pd.Timestamp,
    out_root: Path | None = None,
    latest_path: Path | None = None,
) -> tuple[Path, Path]:
    """Write the ``time_hour,predicted_water_temp,air_temp`` CSV the downstream service consumes.

    Timestamps (and the partition path) are in the site's local timezone
    (``site.timezone_local``, Europe/London), not UTC.

    Saved twice: a timestamped partition ``<root>/<YYYY>/<MM>/<DD>/<HH>.csv``
    keyed on the local issue hour, and a stable "latest" copy. The latter defaults
    to ``<root>/latest.csv`` but ``latest_path`` overrides its full path (e.g. to
    point it at a location the downstream service reads from directly).
    """
    local_tz = data_config()["site"]["timezone_local"]
    out_root = out_root or repo_path("forecast")
    latest = Path(latest_path) if latest_path else out_root / "latest.csv"
    simple = df[["time", "water_temp_pred", "air_temp"]].rename(
        columns={"time": "time_hour", "water_temp_pred": "predicted_water_temp"}
    )
    # Convert to local time, then drop the tz so the column is a bare local
    # string (e.g. "2026-06-06 19:00:00") with no offset, as the consumer expects.
    simple["time_hour"] = (
        pd.to_datetime(simple["time_hour"], utc=True).dt.tz_convert(local_tz).dt.tz_localize(None)
    )
    issue_local = pd.Timestamp(issue_time).tz_convert(local_tz)
    partition = (
        out_root / f"{issue_local:%Y}" / f"{issue_local:%m}" / f"{issue_local:%d}" / f"{issue_local:%H}.csv"
    )
    partition.parent.mkdir(parents=True, exist_ok=True)
    simple.to_csv(partition, index=False)
    latest.parent.mkdir(parents=True, exist_ok=True)
    simple.to_csv(latest, index=False)
    return partition, latest


def _read_state(state_path: Path, max_age_hours: int = 24) -> dict[str, Any]:
    if not state_path.exists():
        raise FileNotFoundError(
            f"state file missing: {state_path} — run `forecast-lake --update-state --observed <temp>` first"
        )
    payload = json.loads(state_path.read_text())
    last_obs = pd.Timestamp(payload["last_obs_time"])
    if last_obs.tzinfo is None:
        last_obs = last_obs.tz_localize("UTC")
    age = pd.Timestamp.now(tz="UTC") - last_obs
    if age > pd.Timedelta(hours=max_age_hours):
        raise RuntimeError(
            f"state is {age} old (> {max_age_hours}h); refresh with --update-state before forecasting"
        )
    return payload


def forecast(
    issue_time: str | datetime | None = None,
    state_path: Path | None = None,
    horizon_hours: int | None = None,
) -> pd.DataFrame:
    tcfg = train_config()
    horizon_hours = int(horizon_hours or tcfg["forecast"]["horizon_hours"])
    state_path = state_path or repo_path("models/state.json")

    if issue_time is None:
        issue_ts = pd.Timestamp.now(tz="UTC").floor("h")
    elif isinstance(issue_time, str):
        issue_ts = pd.Timestamp(issue_time)
        if issue_ts.tzinfo is None:
            issue_ts = issue_ts.tz_localize("UTC")
        else:
            issue_ts = issue_ts.tz_convert("UTC")
    else:
        issue_ts = pd.Timestamp(issue_time, tz="UTC")

    state = _read_state(Path(state_path))
    t0 = float(state["last_water_temp"])

    # One forecast-endpoint pull spanning [issue - tail, issue + horizon]. The
    # archive API can't serve the ~5 days before `now`, so for live issue times
    # both the recent tail and the future come from the forecast endpoint.
    window = fetch_forecast(
        issue_ts.to_pydatetime(), horizon_hours, past_hours=WEATHER_TAIL_HOURS
    )
    weather_tail = window[window.index < issue_ts]
    fc = window[window.index >= issue_ts]

    fm = build_inference_matrix(
        issue_time=issue_ts,
        water_temp_t0=t0,
        forecast_weather=fc,
        historical_weather_tail=weather_tail,
        horizon_hours=horizon_hours,
    )

    # Dispatch on the deployed model type recorded in metadata.json.
    best_dir = repo_path("models/best")
    meta_path = best_dir / "metadata.json"
    if not meta_path.exists():
        raise FileNotFoundError(f"no trained best model at {best_dir}")
    meta = json.loads(meta_path.read_text())
    model_kind = meta.get("model", "")

    if model_kind == "gru":
        preds, target_times = _predict_gru(
            best_dir / "model.pt",
            issue_ts,
            t0,
            weather_tail,
            fc,
            horizon_hours,
        )
        out = pd.DataFrame(
            {
                "time": pd.to_datetime(target_times, utc=True),
                "horizon_h": np.arange(1, horizon_hours + 1, dtype=int),
                "water_temp_pred": preds,
            }
        )
    else:
        model_path = best_dir / "model.joblib"
        if not model_path.exists():
            raise FileNotFoundError(f"no trained best model at {model_path}")
        expected_cols = list(meta["feature_columns"])
        missing = [c for c in expected_cols if c not in fm.X.columns]
        if missing:
            raise RuntimeError(f"inference features missing columns: {missing[:6]}…")
        X = fm.X[expected_cols]
        model = joblib.load(model_path)
        preds = model.predict(X)
        out = pd.DataFrame(
            {
                "time": pd.to_datetime(fm.target_time, utc=True),
                "horizon_h": fm.horizon.astype(int).to_numpy(),
                "water_temp_pred": preds,
            }
        )
    # Carry the Open-Meteo forecast air temperature (temperature_2m) for each
    # target hour. reindex+ffill covers the final hour, which the forecast
    # window's exclusive upper bound can leave just short.
    air = fc["temperature_2m"].reindex(pd.DatetimeIndex(out["time"])).ffill()
    out["air_temp"] = air.to_numpy(dtype=float)
    out["daytime_mask"] = daytime_mask(out["time"])
    return out


def _predict_gru(
    ckpt_path: Path,
    issue_ts: pd.Timestamp,
    t0: float,
    archive_tail: pd.DataFrame,
    forecast_weather: pd.DataFrame,
    horizon_hours: int,
) -> tuple[np.ndarray, pd.DatetimeIndex]:
    """Load a GRU checkpoint and forecast 168 hours.

    Encoder: 72h of weather (+rolling features) ending at issue_time, prepended
    with the t0 anchor channel held constant.
    Decoder: 168h of forecast weather + calendar, prepended with the t0 anchor.
    """
    import torch  # local import to keep startup time low

    from lake_forecast.features.weather import rolling_weather_features
    from lake_forecast.models.neural import WEATHER_VARS, GRUConfig, _Seq2Seq

    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    cfg = GRUConfig(**ckpt["cfg"])

    # Assemble a 72h+168h weather frame.
    weather = pd.concat([archive_tail, forecast_weather]).sort_index()
    weather = weather[~weather.index.duplicated(keep="last")]
    weather = weather[WEATHER_VARS]
    roll = rolling_weather_features(weather)
    weather_features = pd.concat([weather, roll], axis=1)

    # Encoder: rows ending AT issue_ts inclusive
    enc_end = issue_ts
    enc_start = enc_end - pd.Timedelta(hours=cfg.history_hours - 1)
    enc_window = weather_features.loc[enc_start:enc_end]
    if len(enc_window) < cfg.history_hours:
        raise RuntimeError(
            f"insufficient encoder history: got {len(enc_window)} rows, need {cfg.history_hours}"
        )
    enc_window = enc_window.iloc[-cfg.history_hours :]

    target_times = pd.DatetimeIndex(
        [issue_ts + pd.Timedelta(hours=h) for h in range(1, cfg.horizon_hours + 1)]
    )
    dec_window = weather_features.reindex(target_times)
    if dec_window.isna().any().any():
        # forward-fill small gaps; very unusual at the 168h horizon though
        dec_window = dec_window.ffill().bfill()

    cal = pd.DataFrame(index=target_times)
    cal["hour_sin"] = np.sin(2 * np.pi * target_times.hour / 24.0)
    cal["hour_cos"] = np.cos(2 * np.pi * target_times.hour / 24.0)
    cal["doy_sin"] = np.sin(2 * np.pi * target_times.dayofyear / 365.25)
    cal["doy_cos"] = np.cos(2 * np.pi * target_times.dayofyear / 365.25)

    enc_mat = enc_window.to_numpy(dtype=np.float32)
    enc_t0 = np.full((cfg.history_hours, 1), t0, dtype=np.float32)
    enc = np.concatenate([enc_t0, enc_mat], axis=1)

    dec_feat = pd.concat([dec_window, cal], axis=1)
    dec_mat = dec_feat.to_numpy(dtype=np.float32)
    dec_t0 = np.full((cfg.horizon_hours, 1), t0, dtype=np.float32)
    dec = np.concatenate([dec_t0, dec_mat], axis=1)

    enc_mean = np.asarray(ckpt["enc_mean"], dtype=np.float32)
    enc_std = np.asarray(ckpt["enc_std"], dtype=np.float32)
    dec_mean = np.asarray(ckpt["dec_mean"], dtype=np.float32)
    dec_std = np.asarray(ckpt["dec_std"], dtype=np.float32)
    enc = (enc - enc_mean) / enc_std
    dec = (dec - dec_mean) / dec_std

    model = _Seq2Seq(n_enc_feats=enc.shape[1], n_dec_feats=dec.shape[1], cfg=cfg)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()

    with torch.no_grad():
        enc_t = torch.from_numpy(enc).unsqueeze(0)
        dec_t = torch.from_numpy(dec).unsqueeze(0)
        pred = model(enc_t, dec_t).squeeze(0).numpy()
    return pred, target_times
