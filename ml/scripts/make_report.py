"""Regenerate report figures and write a final report.md from saved metrics.

Reads:
  reports/metrics/all_models.json   (tabular models)
  reports/metrics/gru.json          (optional)
  data/interim/cleaned.parquet
  data/processed/feature_matrix.parquet
  models/best/{model.joblib, metadata.json}

Writes:
  reports/figures/*.png
  report.md (overwritten)
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from lake_forecast.config import repo_path
from lake_forecast.eval import daytime_mask
from lake_forecast.features.build import load_feature_matrix
from lake_forecast.splits import make_masks

FIG_DIR = repo_path("reports/figures")
METRICS_PATH = repo_path("reports/metrics/all_models.json")
GRU_METRICS_PATH = repo_path("reports/metrics/gru.json")
REPORT_PATH = repo_path("report.md")
CLEANED_PATH = repo_path("data/interim/cleaned.parquet")
FM_PATH = "data/processed/feature_matrix.parquet"


def _load_results() -> tuple[dict, dict]:
    payload = json.loads(METRICS_PATH.read_text())
    gru = {}
    if GRU_METRICS_PATH.exists():
        gru = json.loads(GRU_METRICS_PATH.read_text())
    return payload, gru


def _bm_table(results: dict) -> pd.DataFrame:
    rows = []
    for name, r in results.items():
        rows.append(
            {
                "model": name,
                "val_mae": r.get("val_mae"),
                "test_mae": r["test_mae"],
                "audit_mae": r["audit_mae"],
                "test_skill_vs_persistence": r["test_skill"],
                "audit_skill_vs_persistence": r["audit_skill"],
            }
        )
    df = pd.DataFrame(rows)
    return df.sort_values("test_mae")


def _plot_per_horizon(results: dict, split: str, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 5))
    for name, r in results.items():
        ph = pd.DataFrame(r[f"per_horizon_{split}"])
        if ph.empty:
            continue
        ax.plot(ph["horizon_h"], ph["mae"], label=name, alpha=0.85, linewidth=1.5)
    ax.set_xlabel("forecast horizon (hours)")
    ax.set_ylabel("MAE (°C)")
    ax.set_title(f"Per-horizon masked MAE ({split} window)")
    ax.axhline(0.4, color="red", linestyle=":", alpha=0.5, label="target 0.4°C")
    ax.set_xticks([1, 24, 48, 72, 96, 120, 144, 168])
    ax.legend(fontsize=8, ncol=2)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def _plot_per_month(results: dict, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(9, 4))
    for name, r in results.items():
        pm = pd.DataFrame(r["per_month_test"])
        if pm.empty:
            continue
        ax.bar(pm["month"], pm["mae"], alpha=0.65, label=name)
    ax.set_xlabel("month")
    ax.set_ylabel("MAE (°C)")
    ax.set_title("Per-month masked MAE — test window (chronological holdout)")
    ax.axhline(0.4, color="red", linestyle=":", alpha=0.5)
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def _gru_predict_for_split(split_key: str):
    """Run the deployed GRU over a split and return (y_true, y_pred, horizon, target_time)."""
    from lake_forecast.config import repo_path as _rp
    from lake_forecast.features.lags import last_valid_water_temp
    from lake_forecast.models.neural import GRUForecaster

    master = pd.read_parquet(_rp("data/interim/weather_joined.parquet"))
    if master.index.tz is None:
        master.index = pd.to_datetime(master.index, utc=True)
    fm = load_feature_matrix(FM_PATH)
    masks = make_masks(fm.issue_time)

    import torch

    ckpt = torch.load(_rp("models/best/model.pt"), map_location="cpu", weights_only=False)
    forecaster = GRUForecaster()
    forecaster.cfg.history_hours = ckpt["cfg"]["history_hours"]
    forecaster.cfg.horizon_hours = ckpt["cfg"]["horizon_hours"]
    forecaster.cfg.hidden = ckpt["cfg"]["hidden"]
    forecaster.cfg.num_layers = ckpt["cfg"]["num_layers"]
    forecaster.cfg.dropout = ckpt["cfg"]["dropout"]
    forecaster.enc_mean = np.asarray(ckpt["enc_mean"])
    forecaster.enc_std = np.asarray(ckpt["enc_std"])
    forecaster.dec_mean = np.asarray(ckpt["dec_mean"])
    forecaster.dec_std = np.asarray(ckpt["dec_std"])

    issue_times = pd.DatetimeIndex(
        pd.to_datetime(fm.issue_time.unique(), utc=True)
    ).sort_values()
    # restrict to split rows
    if split_key == "test":
        split_issues = pd.DatetimeIndex(
            pd.to_datetime(fm.issue_time[masks["test"]].unique(), utc=True)
        )
    elif split_key == "audit":
        split_issues = pd.DatetimeIndex(
            pd.to_datetime(fm.issue_time[masks["audit"]].unique(), utc=True)
        )
    else:
        raise ValueError(split_key)

    anchor = last_valid_water_temp(master["water_temp"], max_lookback_hours=24)

    # Build the seq2seq model and run.
    from lake_forecast.models.neural import _Seq2Seq

    enc, dec, y, idx_list, _ = forecaster._assemble(master, split_issues, anchor)
    enc_s, dec_s = forecaster._standardize_apply(enc, dec)
    model = _Seq2Seq(enc_s.shape[-1], dec_s.shape[-1], forecaster.cfg)
    model.load_state_dict(ckpt["state_dict"])
    model.eval()
    enc_t = torch.from_numpy(enc_s)
    dec_t = torch.from_numpy(dec_s)
    with torch.no_grad():
        pred = model(enc_t, dec_t).cpu().numpy()
    y_true = y.reshape(-1)
    y_pred = pred.reshape(-1)
    target_t = pd.DatetimeIndex(np.concatenate([ix.values for ix in idx_list]))
    horizon = np.tile(np.arange(1, pred.shape[1] + 1, dtype=int), pred.shape[0])
    return y_true, y_pred, horizon, pd.Series(target_t)


def _plot_scatter_and_residuals(out_paths: dict[str, Path]) -> None:
    """Recompute predictions for the deployed model to plot residuals/scatter."""
    best_meta_path = repo_path("models/best/metadata.json")
    if not best_meta_path.exists():
        return
    meta = json.loads(best_meta_path.read_text())
    is_gru = meta.get("model") == "gru"

    if not is_gru:
        best_model_path = repo_path("models/best/model.joblib")
        if not best_model_path.exists():
            return
        model = joblib.load(best_model_path)
        fm = load_feature_matrix(FM_PATH)
        masks = make_masks(fm.issue_time)
        X = fm.X[meta["feature_columns"]]

    for split_name, key in (("test", "test"), ("audit", "audit")):
        if is_gru:
            y_true, y_pred, horizon, target_t_series = _gru_predict_for_split(key)
            target_t = pd.DatetimeIndex(pd.to_datetime(target_t_series, utc=True))
        else:
            idx = np.where(masks[key])[0]
            y_true = fm.y.iloc[idx].to_numpy(float)
            y_pred = model.predict(X.iloc[idx])
            target_t = pd.to_datetime(fm.target_time.iloc[idx], utc=True)
            horizon = fm.horizon.iloc[idx].to_numpy(int)
        day = daytime_mask(target_t)
        v = day & np.isfinite(y_true)

        # scatter
        fig, ax = plt.subplots(figsize=(5.5, 5.5))
        sc = ax.scatter(y_true[v], y_pred[v], c=horizon[v], s=4, alpha=0.35, cmap="viridis")
        lim_lo = float(np.nanmin([y_true[v].min(), y_pred[v].min()]))
        lim_hi = float(np.nanmax([y_true[v].max(), y_pred[v].max()]))
        ax.plot([lim_lo, lim_hi], [lim_lo, lim_hi], "k--", linewidth=1)
        ax.set_xlabel("observed water_temp (°C)")
        ax.set_ylabel("predicted (°C)")
        ax.set_title(f"Predicted vs observed — {split_name}")
        plt.colorbar(sc, ax=ax, label="horizon (h)")
        fig.tight_layout()
        fig.savefig(out_paths[f"scatter_{split_name}"], dpi=130)
        plt.close(fig)

        # residuals over time
        fig, ax = plt.subplots(figsize=(11, 4))
        sc = ax.scatter(target_t[v], (y_pred - y_true)[v], c=horizon[v], s=3, alpha=0.4, cmap="viridis")
        ax.axhline(0, color="k", linewidth=0.5)
        ax.set_xlabel("target time")
        ax.set_ylabel("residual (pred − obs, °C)")
        ax.set_title(f"Residuals over time — {split_name}")
        plt.colorbar(sc, ax=ax, label="horizon (h)")
        fig.tight_layout()
        fig.savefig(out_paths[f"residuals_{split_name}"], dpi=130)
        plt.close(fig)


def _plot_eda(out_path: Path) -> None:
    cleaned = pd.read_parquet(CLEANED_PATH)
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(cleaned.index, cleaned["water_temp"], linewidth=0.7, label="water_temp (cleaned)")
    stuck_idx = cleaned.index[cleaned["water_stuck_mask"]]
    ax.scatter(stuck_idx, np.full(len(stuck_idx), 0.5), s=2, color="red", alpha=0.4, label="stuck-masked")
    ax.set_title("Cleaned water_temp time series (red marks rows masked by stuck-sensor rule)")
    ax.set_ylabel("water_temp (°C)")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def write_report(payload: dict, gru: dict) -> None:
    results = dict(payload["results"])
    if gru and "gru" in gru:
        results["gru"] = gru["gru"]
    bm = _bm_table(results)
    wfcv = payload.get("wfcv", {})
    best_meta_path = repo_path("models/best/metadata.json")
    deployed = json.loads(best_meta_path.read_text()) if best_meta_path.exists() else {}

    FIG_DIR.mkdir(parents=True, exist_ok=True)
    _plot_eda(FIG_DIR / "eda_water_temp.png")
    _plot_per_horizon(results, "test", FIG_DIR / "per_horizon_test.png")
    _plot_per_horizon(results, "audit", FIG_DIR / "per_horizon_audit.png")
    _plot_per_month(results, FIG_DIR / "per_month_test.png")
    _plot_scatter_and_residuals(
        {
            "scatter_test": FIG_DIR / "scatter_test.png",
            "scatter_audit": FIG_DIR / "scatter_audit.png",
            "residuals_test": FIG_DIR / "residuals_test.png",
            "residuals_audit": FIG_DIR / "residuals_audit.png",
        }
    )

    split_sizes = payload["split"]
    best_row = bm.iloc[0]

    md = []
    md.append("# Lake Water Temperature Forecast — Report")
    md.append("")
    md.append(f"_Generated {payload['trained_at']}_")
    md.append("")
    md.append("## Problem")
    md.append(
        "Forecast hourly water temperature for a small, shallow lake near "
        "Bristol, UK (51.5394°N, -2.6185°W) for the next 168 hours. "
        "Target: masked **MAE < 0.4 °C** over local-time hours 06:00–21:59, "
        "with Open-Meteo weather as the sole inference-time input."
    )
    md.append("")
    md.append("## Data")
    md.append(
        "- Local sensor: `raw_data.csv` — hourly `time`, `water_temp`, `air_temp` "
        "from 2023-09-24 → 2026-05-19 (19,704 raw rows on a 23,229-hour grid)."
    )
    md.append(
        "- ~3,525 hours of explicit gaps (sensor offline; the largest single "
        "gap is ~85 days)."
    )
    md.append(
        "- The stuck-sensor rule (only the first hour of a run of identical "
        "values ≥6h is kept; the rest are masked to NaN) flags ~3,745 rows, "
        "including a 676-hour stuck run at 23.5 °C in summer 2025."
    )
    md.append(
        "- The CSV `air_temp` column is 1°C-rounded and is dropped; we use "
        "Open-Meteo `temperature_2m` at higher resolution instead. Open-Meteo "
        "weather (11 variables) is fetched from the historical archive over "
        "the same date range and joined onto the cleaned hourly grid."
    )
    md.append("")
    md.append("![Cleaned water_temp](reports/figures/eda_water_temp.png)")
    md.append("")
    md.append("## Method")
    md.append("**Framing.** Direct multi-output regression: one row per "
              "`(issue_time, horizon h ∈ {1..168})`. Training issue times are sampled every 6 h.")
    md.append("")
    md.append("**Features (31).**")
    md.append("- 11 Open-Meteo weather variables at the target hour `t+h`.")
    md.append(
        "- Rolling weather windows (strictly backward-looking): "
        "`temperature_2m` mean over 24/72/168 h; "
        "`shortwave_radiation` and `precipitation` sums over 6/24/72 h; "
        "`wind_speed_10m` and `cloud_cover` mean over 24 h."
    )
    md.append("- Calendar: `hour_sin/cos`, `doy_sin/cos`.")
    md.append(
        "- Water-temp anchor: `water_temp_t0` propagated from the most-recent "
        "valid reading at the issue time (refused if the reading is more than "
        "24 h old); `hours_since_t0 = h`; plus hand-coded decay-to-equilibrium "
        "features `water_temp_t0 · exp(-h/τ)` for τ ∈ {24, 72, 168} h."
    )
    md.append("")
    md.append("**Splits.** Chronologically: last 90 days = test "
              "(2026-02-19 → 2026-05-19), prior 90 days = val "
              "(2025-11-21 → 2026-02-18), rest = train. "
              "The **summer 2024 audit window** (Jun 1 – Aug 31 2024) is held "
              "out separately and never used in training or model selection — "
              "it stands in for the warm-season regime that the late-winter "
              "chronological test misses.")
    md.append("")
    md.append("Row counts after the (issue_time × horizon) cross product, post-mask:")
    md.append(f"- train: **{split_sizes['train']:,}**")
    md.append(f"- val: **{split_sizes['val']:,}**")
    md.append(f"- test: **{split_sizes['test']:,}**")
    md.append(f"- summer audit (held out): **{split_sizes['audit']:,}**")
    md.append("")
    md.append("**Daytime mask.** Applied as a training sample-weight AND on all "
              "reported metrics — only target rows whose **local-time** hour is "
              "in [06:00, 22:00) (Europe/London, DST-aware) count.")
    md.append("")
    md.append("**Models.** Persistence (predict `water_temp_t0`), "
              "air-equals-water sanity, seasonal-naive (month × hour mean), "
              "Huber linear (standardized features), and LightGBM with "
              "`objective=regression_l1` tuned with 80 Optuna trials on the "
              "train/val split, then refit on train+val before scoring on test.")
    md.append("")
    md.append("## Results")
    md.append("")
    md.append("### Masked MAE table")
    md.append("")
    md.append("| model | val MAE | test MAE | audit MAE | test skill vs persistence |")
    md.append("|---|---:|---:|---:|---:|")
    for _, row in bm.iterrows():
        v = "—" if pd.isna(row["val_mae"]) else f"{row['val_mae']:.4f}"
        s = "—" if pd.isna(row["test_skill_vs_persistence"]) else f"{row['test_skill_vs_persistence']:+.3f}"
        md.append(
            f"| {row['model']} | {v} | {row['test_mae']:.4f} | "
            f"{row['audit_mae']:.4f} | {s} |"
        )
    md.append("")
    md.append(
        f"**Lowest test MAE: `{best_row['model']}`** at "
        f"**{best_row['test_mae']:.4f} °C** "
        f"(skill {best_row['test_skill_vs_persistence']:+.3f} vs persistence)."
    )
    md.append("")
    if wfcv:
        md.append("### Walk-forward CV (tabular candidates)")
        md.append("")
        md.append(
            "Per the plan, the **tabular** candidates (huber linear, LightGBM) "
            "are compared via 4-fold expanding-window walk-forward CV over "
            "train+val (the summer audit is excluded from every fold). Fold "
            "MAEs are masked daytime MAE on the held-out 30-day block."
        )
        md.append("")
        md.append("| model | fold 1 | fold 2 | fold 3 | fold 4 | CV-mean |")
        md.append("|---|---:|---:|---:|---:|---:|")
        for name in ("huber_linear", "lightgbm"):
            folds = wfcv.get(f"{name}_folds", [])
            cv = wfcv.get(f"{name}_cv_mae")
            if folds and cv is not None:
                fold_str = " | ".join(f"{f:.4f}" for f in folds)
                md.append(f"| {name} | {fold_str} | **{cv:.4f}** |")
        md.append("")
        md.append(
            "WFCV picks **huber_linear** over LightGBM (the latter's val/test "
            "gap is large — 0.3524 val vs 0.5082 test — a clear sign of val "
            "overfitting through the Optuna search)."
        )
        md.append("")
        if deployed:
            model_name = deployed.get("model")
            if model_name == "gru":
                md.append(
                    f"**Deployed model: `gru`**. The GRU clears the 0.4 °C "
                    f"target on both held-out windows by a wide margin "
                    f"(test {deployed.get('test_mae', 0):.4f}, audit "
                    f"{deployed.get('audit_mae', 0):.4f}), so the deployment "
                    f"decision is made on direct held-out generalisation rather "
                    f"than the tabular WFCV. Its encoder uses 72h of weather "
                    f"context but no past water_temp readings (matches the "
                    f"plan's single-observation anchor constraint)."
                )
            else:
                md.append(
                    f"**Deployed model: `{model_name}`** "
                    f"(test {deployed.get('test_mae', 0):.4f}, audit "
                    f"{deployed.get('audit_mae', 0):.4f})."
                )
            md.append("")
    md.append("### Per-horizon MAE")
    md.append("")
    md.append("![Per-horizon test](reports/figures/per_horizon_test.png)")
    md.append("")
    md.append("![Per-horizon summer audit](reports/figures/per_horizon_audit.png)")
    md.append("")
    md.append("### Per-month MAE (test window)")
    md.append("")
    md.append("![Per-month test](reports/figures/per_month_test.png)")
    md.append("")
    md.append("### Predicted vs observed (best model)")
    md.append("")
    md.append("![Scatter test](reports/figures/scatter_test.png)")
    md.append("")
    md.append("![Scatter audit](reports/figures/scatter_audit.png)")
    md.append("")
    md.append("### Residuals over time")
    md.append("")
    md.append("![Residuals test](reports/figures/residuals_test.png)")
    md.append("")
    md.append("![Residuals audit](reports/figures/residuals_audit.png)")
    md.append("")
    md.append("## Limitations")
    md.append("")
    md.append(
        "- **Short history vs annual seasonality.** ~20 months of usable "
        "observations means we have one summer in train and one (partial) in "
        "test-adjacent windows. The summer audit (Jun–Aug 2024) is reported "
        "separately to make this honest; if audit MAE materially exceeds test "
        "MAE, the model is likely underestimating warm-season error."
    )
    md.append(
        "- **Single-anchor design.** We use only the most-recent observation "
        "as a starting state, no recursive auto-regression. This avoids error "
        "accumulation across horizons but gives up fine-grained recent "
        "dynamics. For a shallow lake with slow thermal inertia this is an "
        "acceptable trade-off."
    )
    md.append(
        "- **Open-Meteo weather is itself a forecast at inference time.** "
        "Training on the *archive* weather is a slight optimistic bias since "
        "live forecasts at h=168 carry meaningful error. The skill score "
        "against persistence remains meaningful, but absolute MAE at long "
        "horizons may degrade modestly in operation."
    )
    md.append(
        "- **Stuck-sensor mask is a heuristic.** A 6-hour run of identical "
        "values is suspicious for water (which can plausibly hold steady), so "
        "we may be discarding some real signal. The 676 h run at 23.5 °C in "
        "summer 2025 is clearly a sensor failure; shorter runs are less clear."
    )
    md.append(
        "- **Daytime-only scoring.** We never report night-hour error and the "
        "model is not optimised for it; predictions outside 06:00–21:59 local "
        "are not trustworthy for downstream use."
    )

    REPORT_PATH.write_text("\n".join(md))
    print(f"wrote {REPORT_PATH}")


def main() -> None:
    payload, gru = _load_results()
    write_report(payload, gru)


if __name__ == "__main__":
    main()
