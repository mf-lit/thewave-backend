"""Periodic retrain-and-promote for the deployed GRU.

Run on a slow cadence (monthly). Each run:

1. Refreshes training data: re-clean from the live source CSV and re-fetch the
   Open-Meteo archive up to ~now, then rebuild the master table + feature matrix.
2. Derives **rolling** split boundaries from the latest issue time (so a month of
   formerly-validation data rolls into training each run).
3. Trains a fresh GRU on the rolling split.
4. Scores the deployed incumbent on the *same* rolling test+audit windows.
5. Promotes the candidate only if it is strictly better (lower test+audit MAE).

The previous `models/best/` is snapshotted to `models/archive/<ts>/` before any
promotion so a regression can be rolled back.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from lake_forecast.config import repo_path, train_config
from lake_forecast.features.lags import last_valid_water_temp
from lake_forecast.models.neural import GRUForecaster
from lake_forecast.splits import SplitSpec, rolling_split_spec
from lake_forecast.train import evaluate_gru_on_master, train_gru


def refresh_training_data() -> pd.DataFrame:
    """Re-clean, re-fetch archive weather up to now, rebuild master + features.

    Returns the rebuilt master table. Bypasses the on-disk caches that
    ``build_master_table`` would otherwise reuse, so new readings and recent
    weather actually make it into the feature matrix.
    """
    from lake_forecast.config import data_config
    from lake_forecast.data.align import build_master_table
    from lake_forecast.data.clean import run_clean
    from lake_forecast.features.build import build_training_matrix, save_feature_matrix
    from lake_forecast.io.openmeteo import fetch_archive

    cfg = data_config()

    # 1. Fresh clean from the live source CSV (overwrites cleaned.parquet).
    run_clean()

    # 2. Refresh archive weather up to now. ERA5T serves up to ~now-5d; the join
    #    masks the unavailable tail. Shard cache makes this cheap after the first run.
    start = datetime.fromisoformat(cfg["date_range"]["history_start"]).replace(tzinfo=UTC)
    weather = fetch_archive(start, datetime.now(UTC))
    weather_path = repo_path("data/interim/weather_archive.parquet")
    weather_path.parent.mkdir(parents=True, exist_ok=True)
    weather.to_parquet(weather_path)

    # 3. Rebuild master (reads the fresh cleaned + weather) and feature matrix.
    master = build_master_table()
    fm = build_training_matrix(master)
    save_feature_matrix(fm, cfg["paths"]["feature_matrix_parquet"])
    print(f"refreshed: master rows={len(master)}  feature rows={len(fm.X)}")
    return master


def _combined_mae(test_mae: float, audit_mae: float) -> float:
    """Selection score: lower test+audit masked MAE (matches the deploy rule)."""
    return float(test_mae) + float(audit_mae)


def _snapshot_best(best_dir: Path, stamp: str) -> Path | None:
    if not best_dir.exists() or not any(best_dir.iterdir()):
        return None
    dest = repo_path("models/archive") / stamp
    shutil.copytree(best_dir, dest)
    return dest


def retrain_and_maybe_promote(
    test_days: int | None = None,
    val_days: int | None = None,
) -> dict:
    """Train a candidate GRU on a rolling split and promote it iff strictly better.

    Returns a decision dict (promoted flag + candidate/incumbent metrics).
    """
    rcfg = train_config().get("retrain", {})
    test_days = int(test_days or rcfg.get("test_days", 90))
    val_days = int(val_days or rcfg.get("val_days", 90))

    master = refresh_training_data()
    from lake_forecast.features.build import load_feature_matrix

    fm = load_feature_matrix("data/processed/feature_matrix.parquet")
    issue_grid = pd.DatetimeIndex(pd.to_datetime(fm.issue_time.unique(), utc=True)).sort_values()
    latest_issue = issue_grid.max()
    spec = rolling_split_spec(latest_issue, test_days=test_days, val_days=val_days)
    print(
        f"rolling split  train<{spec.val_start.date()}  "
        f"val[{spec.val_start.date()}..{spec.test_start.date()})  "
        f"test>={spec.test_start.date()}  (latest issue {latest_issue.date()})"
    )

    # --- candidate ---
    candidate = train_gru(spec=spec)
    if candidate is None:
        raise RuntimeError("candidate GRU training produced no metrics (empty test window?)")

    # --- incumbent, scored on the SAME rolling windows ---
    incumbent_metrics = _score_incumbent(master, fm, spec)

    cand_score = _combined_mae(candidate.test_mae, candidate.audit_mae)
    decision = {
        "candidate": {"test_mae": candidate.test_mae, "audit_mae": candidate.audit_mae},
        "incumbent": incumbent_metrics,
        "test_days": test_days,
        "val_days": val_days,
        "rolling_test_start": spec.test_start.isoformat(),
    }

    if incumbent_metrics is None:
        print("no comparable incumbent (missing or non-GRU) — promoting candidate")
        decision["promoted"] = True
        decision["reason"] = "no comparable incumbent"
    else:
        inc_score = _combined_mae(incumbent_metrics["test_mae"], incumbent_metrics["audit_mae"])
        better = cand_score < inc_score  # strictly better
        decision["promoted"] = better
        decision["candidate_combined_mae"] = cand_score
        decision["incumbent_combined_mae"] = inc_score
        decision["reason"] = (
            f"candidate {cand_score:.4f} {'<' if better else '>='} incumbent {inc_score:.4f}"
        )
        print(f"gate: {decision['reason']} → {'PROMOTE' if better else 'KEEP incumbent'}")

    if decision["promoted"]:
        _promote(candidate)

    return decision


def _score_incumbent(master: pd.DataFrame, fm, spec: SplitSpec) -> dict | None:
    """Score the currently deployed model on the rolling test+audit windows.

    Only GRU incumbents are comparable through this path; returns None otherwise
    (or if no model is deployed yet).
    """
    best_dir = repo_path("models/best")
    meta_path = best_dir / "metadata.json"
    model_pt = best_dir / "model.pt"
    if not meta_path.exists() or not model_pt.exists():
        return None
    if json.loads(meta_path.read_text()).get("model") != "gru":
        return None

    if master.index.tz is None:
        master = master.copy()
        master.index = pd.to_datetime(master.index, utc=True)
    anchor = last_valid_water_temp(master["water_temp"], max_lookback_hours=24)
    issue_grid = pd.DatetimeIndex(pd.to_datetime(fm.issue_time.unique(), utc=True)).sort_values()
    test_issue = issue_grid[issue_grid >= spec.test_start]
    audit_issue = issue_grid[(issue_grid >= spec.audit_start) & (issue_grid <= spec.audit_end)]

    incumbent = GRUForecaster.from_checkpoint(model_pt)
    te = evaluate_gru_on_master(incumbent, master, anchor, test_issue)
    au = evaluate_gru_on_master(incumbent, master, anchor, audit_issue)
    if te is None or au is None:
        return None
    return {"test_mae": te.mae, "audit_mae": au.mae}


def _promote(candidate) -> None:
    """Snapshot the old best, then install the freshly trained gru.pt as deployed."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    best_dir = repo_path("models/best")
    archived = _snapshot_best(best_dir, stamp)
    if archived:
        print(f"archived previous best → {archived}")

    best_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(repo_path("models/gru.pt"), best_dir / "model.pt")
    legacy = best_dir / "model.joblib"
    if legacy.exists():
        legacy.unlink()
    (best_dir / "metadata.json").write_text(
        json.dumps(
            {
                "model": "gru",
                "selection_rule": "retrain: strictly lower test+audit MAE vs incumbent on rolling split",
                "test_mae": candidate.test_mae,
                "audit_mae": candidate.audit_mae,
                "test_skill_vs_persistence": candidate.test_skill,
                "audit_skill_vs_persistence": candidate.audit_skill,
                "trained_at": datetime.now(UTC).isoformat(),
                "archived_previous": stamp if archived else None,
            },
            indent=2,
        )
    )
    print(f"promoted candidate gru: test={candidate.test_mae:.4f}  audit={candidate.audit_mae:.4f}")
