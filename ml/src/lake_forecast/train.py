"""Training orchestration.

Coordinates baseline + linear + LightGBM (with Optuna) + GRU runs over the
same train/val/test/audit split, writes metrics + artifacts.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import joblib
import numpy as np
import pandas as pd

from lake_forecast.config import data_config, repo_path, train_config
from lake_forecast.eval import daytime_mask, evaluate
from lake_forecast.features.build import (
    FeatureMatrix,
    build_training_matrix,
    load_feature_matrix,
)
from lake_forecast.models.baselines import (
    AirEqualsWaterModel,
    SeasonalNaiveModel,
)
from lake_forecast.models.lightgbm_model import LightGBMModel, optuna_search
from lake_forecast.models.linear import HuberLinearModel
from lake_forecast.models.neural import GRUForecaster
from lake_forecast.splits import SplitSpec, make_masks, split_spec_from_config


@dataclass
class TrainResult:
    name: str
    val_mae: float | None
    test_mae: float
    audit_mae: float
    test_skill: float | None
    audit_skill: float | None
    per_horizon_test: list[dict[str, Any]]
    per_horizon_audit: list[dict[str, Any]]
    per_month_test: list[dict[str, Any]]


def _load_master() -> pd.DataFrame:
    cfg = data_config()
    p = repo_path(cfg["paths"]["weather_joined_parquet"])
    if not p.exists():
        from lake_forecast.data.align import build_master_table

        return build_master_table()
    return pd.read_parquet(p)


def _load_features() -> FeatureMatrix:
    cfg = data_config()
    p = repo_path(cfg["paths"]["feature_matrix_parquet"])
    if not p.exists():
        master = _load_master()
        fm = build_training_matrix(master)
        return fm
    return load_feature_matrix(cfg["paths"]["feature_matrix_parquet"])


def _persistence_pred(fm: FeatureMatrix, idx: np.ndarray) -> np.ndarray:
    return fm.X["water_temp_t0"].iloc[idx].to_numpy(dtype=float)


def _persistence_pred_rows(X: pd.DataFrame) -> np.ndarray:
    return X["water_temp_t0"].to_numpy(dtype=float)


def _make_eval(
    fm: FeatureMatrix,
    mask: np.ndarray,
    y_pred: np.ndarray,
):
    idx = np.where(mask)[0]
    y_true = fm.y.iloc[idx].to_numpy(dtype=float)
    persistence = _persistence_pred(fm, idx)
    return evaluate(
        y_true=y_true,
        y_pred=y_pred,
        horizon=fm.horizon.iloc[idx].to_numpy(dtype=int),
        target_time=fm.target_time.iloc[idx],
        persistence_pred=persistence,
    )


def _sample_weights_daytime(fm: FeatureMatrix, idx: np.ndarray) -> np.ndarray:
    """Daytime mask in [0,1] for the given row indices."""
    return daytime_mask(fm.target_time.iloc[idx]).astype(float)


def train_all(
    optuna_trials: int = 80,
    skip: tuple[str, ...] = (),
    metrics_dir: str = "reports/metrics",
    artifacts_dir: str = "models",
) -> dict[str, TrainResult]:
    fm = _load_features()
    masks = make_masks(fm.issue_time)
    tr_idx = np.where(masks["train"])[0]
    va_idx = np.where(masks["val"])[0]
    te_idx = np.where(masks["test"])[0]
    au_idx = np.where(masks["audit"])[0]

    print(
        f"split sizes  train={len(tr_idx):,}  val={len(va_idx):,}  "
        f"test={len(te_idx):,}  audit={len(au_idx):,}"
    )

    X = fm.X
    y = fm.y.to_numpy(dtype=float)

    metrics_path = repo_path(metrics_dir)
    metrics_path.mkdir(parents=True, exist_ok=True)
    artifacts_path = repo_path(artifacts_dir)
    artifacts_path.mkdir(parents=True, exist_ok=True)

    results: dict[str, TrainResult] = {}

    def _run_simple(model_name: str, predict_fn) -> None:
        if model_name in skip:
            return
        print(f"\n=== {model_name} ===")
        te_pred = predict_fn(X.iloc[te_idx])
        au_pred = predict_fn(X.iloc[au_idx])
        va_pred = predict_fn(X.iloc[va_idx]) if len(va_idx) else None
        te = _make_eval(fm, masks["test"], te_pred)
        au = _make_eval(fm, masks["audit"], au_pred)
        va = _make_eval(fm, masks["val"], va_pred) if va_pred is not None else None
        print(f"  test MAE={te.mae:.4f}  audit MAE={au.mae:.4f}")
        results[model_name] = TrainResult(
            name=model_name,
            val_mae=va.mae if va else None,
            test_mae=te.mae,
            audit_mae=au.mae,
            test_skill=te.skill_vs_persistence,
            audit_skill=au.skill_vs_persistence,
            per_horizon_test=te.per_horizon.to_dict(orient="records"),
            per_horizon_audit=au.per_horizon.to_dict(orient="records"),
            per_month_test=te.per_month.to_dict(orient="records"),
        )

    # --- Persistence
    _run_simple("persistence", lambda Xs: _persistence_pred_rows(Xs))

    # --- Air ≈ water
    _run_simple("air_equals_water", AirEqualsWaterModel().predict)

    # --- Seasonal naive
    if "seasonal_naive" not in skip:
        print("\n=== seasonal_naive ===")
        sn = SeasonalNaiveModel()
        sn.fit(X.iloc[tr_idx], y[tr_idx], target_time=fm.target_time.iloc[tr_idx])
        te_pred = sn.predict(X.iloc[te_idx], target_time=fm.target_time.iloc[te_idx])
        au_pred = sn.predict(X.iloc[au_idx], target_time=fm.target_time.iloc[au_idx])
        va_pred = sn.predict(X.iloc[va_idx], target_time=fm.target_time.iloc[va_idx]) if len(va_idx) else None
        te = _make_eval(fm, masks["test"], te_pred)
        au = _make_eval(fm, masks["audit"], au_pred)
        va = _make_eval(fm, masks["val"], va_pred) if va_pred is not None else None
        print(f"  test MAE={te.mae:.4f}  audit MAE={au.mae:.4f}")
        results["seasonal_naive"] = TrainResult(
            name="seasonal_naive",
            val_mae=va.mae if va else None,
            test_mae=te.mae,
            audit_mae=au.mae,
            test_skill=te.skill_vs_persistence,
            audit_skill=au.skill_vs_persistence,
            per_horizon_test=te.per_horizon.to_dict(orient="records"),
            per_horizon_audit=au.per_horizon.to_dict(orient="records"),
            per_month_test=te.per_month.to_dict(orient="records"),
        )

    # --- Huber linear
    if "huber" not in skip:
        print("\n=== huber_linear ===")
        sw = _sample_weights_daytime(fm, tr_idx)
        best_mae = float("inf")
        best_alpha = None
        best_model: HuberLinearModel | None = None
        for alpha in train_config()["models"]["ridge"]["alphas"]:
            mdl = HuberLinearModel(alpha=float(alpha))
            mdl.fit(X.iloc[tr_idx], y[tr_idx], sample_weight=sw)
            preds = mdl.predict(X.iloc[va_idx])
            va_eval = _make_eval(fm, masks["val"], preds)
            print(f"  alpha={alpha:>8.3g}  val MAE={va_eval.mae:.4f}")
            if va_eval.mae < best_mae:
                best_mae = va_eval.mae
                best_alpha = alpha
                best_model = mdl
        assert best_model is not None
        print(f"  → best alpha={best_alpha}, val MAE={best_mae:.4f}")
        joblib.dump(best_model, artifacts_path / "huber_linear.joblib")
        te = _make_eval(fm, masks["test"], best_model.predict(X.iloc[te_idx]))
        au = _make_eval(fm, masks["audit"], best_model.predict(X.iloc[au_idx]))
        print(f"  test MAE={te.mae:.4f}  audit MAE={au.mae:.4f}")
        results["huber_linear"] = TrainResult(
            name="huber_linear",
            val_mae=best_mae,
            test_mae=te.mae,
            audit_mae=au.mae,
            test_skill=te.skill_vs_persistence,
            audit_skill=au.skill_vs_persistence,
            per_horizon_test=te.per_horizon.to_dict(orient="records"),
            per_horizon_audit=au.per_horizon.to_dict(orient="records"),
            per_month_test=te.per_month.to_dict(orient="records"),
        )

    # --- LightGBM (Optuna)
    if "lightgbm" not in skip:
        print("\n=== lightgbm ===")
        sw_tr = _sample_weights_daytime(fm, tr_idx)
        sw_va = _sample_weights_daytime(fm, va_idx)
        best_params = optuna_search(
            X.iloc[tr_idx],
            y[tr_idx],
            X.iloc[va_idx],
            y[va_idx],
            sample_weight_train=sw_tr,
            sample_weight_val=sw_va,
            n_trials=optuna_trials,
            timeout_seconds=int(train_config()["models"]["lightgbm"]["timeout_seconds"]),
        )
        print(f"  best params: {best_params}")
        # Fit final model on train-only with val for honest early-stopping.
        # (Refitting on train+val with val as eval_set would leak.)
        mdl = LightGBMModel(params=best_params, n_estimators=4000)
        mdl.fit(
            X.iloc[tr_idx],
            y[tr_idx],
            sample_weight=sw_tr,
            eval_set=(X.iloc[va_idx], y[va_idx]),
            early_stopping_rounds=200,
        )
        joblib.dump(mdl, artifacts_path / "lightgbm.joblib")
        te = _make_eval(fm, masks["test"], mdl.predict(X.iloc[te_idx]))
        au = _make_eval(fm, masks["audit"], mdl.predict(X.iloc[au_idx]))
        va = _make_eval(fm, masks["val"], mdl.predict(X.iloc[va_idx]))
        print(f"  val MAE={va.mae:.4f}  test MAE={te.mae:.4f}  audit MAE={au.mae:.4f}")
        with open(artifacts_path / "lightgbm_best_params.json", "w") as fh:
            json.dump(best_params, fh, indent=2)
        results["lightgbm"] = TrainResult(
            name="lightgbm",
            val_mae=va.mae,
            test_mae=te.mae,
            audit_mae=au.mae,
            test_skill=te.skill_vs_persistence,
            audit_skill=au.skill_vs_persistence,
            per_horizon_test=te.per_horizon.to_dict(orient="records"),
            per_horizon_audit=au.per_horizon.to_dict(orient="records"),
            per_month_test=te.per_month.to_dict(orient="records"),
        )

    # --- Persist combined metrics
    payload = {
        "trained_at": datetime.now(UTC).isoformat(),
        "split": {k: int(v.sum()) for k, v in masks.items()},
        "results": {k: asdict(v) for k, v in results.items()},
    }
    with open(metrics_path / "all_models.json", "w") as fh:
        json.dump(payload, fh, indent=2)

    # Pick best on val MAE among non-baseline models that have a val MAE.
    candidates = {
        k: v for k, v in results.items() if k in ("huber_linear", "lightgbm") and v.val_mae is not None
    }
    if candidates:
        best_name = min(candidates, key=lambda k: candidates[k].val_mae)  # type: ignore[arg-type]
        best_dir = artifacts_path / "best"
        best_dir.mkdir(parents=True, exist_ok=True)
        src = artifacts_path / f"{best_name}.joblib"
        if src.exists():
            joblib.dump(joblib.load(src), best_dir / "model.joblib")
        with open(best_dir / "metadata.json", "w") as fh:
            json.dump(
                {
                    "model": best_name,
                    "feature_columns": list(X.columns),
                    "val_mae": candidates[best_name].val_mae,
                    "test_mae": candidates[best_name].test_mae,
                    "audit_mae": candidates[best_name].audit_mae,
                    "trained_at": datetime.now(UTC).isoformat(),
                },
                fh,
                indent=2,
            )

    return results


def evaluate_gru_on_master(
    model: GRUForecaster,
    master: pd.DataFrame,
    anchor: pd.Series,
    issue_times: pd.DatetimeIndex,
):
    """Run a (fitted or loaded) GRU over ``issue_times`` and return masked metrics.

    Returns ``None`` for an empty window. Shared by training and the retrain
    gate so the incumbent and candidate are scored through identical code.
    """
    if len(issue_times) == 0:
        return None
    preds, idx_list = model.predict_from_master(master, issue_times, anchor)
    rows = []
    for it, idx, row_pred in zip(issue_times, idx_list, preds, strict=False):
        for h, target_time, pred in zip(range(1, len(idx) + 1), idx, row_pred, strict=False):
            rows.append((it, h, target_time, float(pred)))
    df = pd.DataFrame(rows, columns=["issue_time", "horizon_h", "target_time", "pred"])
    merged = df.merge(
        master["water_temp"].rename("y").to_frame().reset_index().rename(columns={"time": "target_time"}),
        on="target_time",
        how="left",
    )
    return evaluate(
        y_true=merged["y"].to_numpy(),
        y_pred=merged["pred"].to_numpy(),
        horizon=merged["horizon_h"].to_numpy(),
        target_time=merged["target_time"],
        persistence_pred=merged.merge(
            anchor.rename("anchor").to_frame().reset_index().rename(columns={"time": "issue_time"}),
            on="issue_time",
            how="left",
        )["anchor"].to_numpy(),
    )


def train_gru(
    metrics_dir: str = "reports/metrics",
    artifacts_dir: str = "models",
    spec: SplitSpec | None = None,
) -> TrainResult | None:
    """Optional GRU run using the master table directly (not the wide matrix).

    ``spec`` overrides the split boundaries (the periodic retrain passes a
    rolling spec); defaults to the frozen config splits.
    """
    fm = _load_features()
    master = _load_master()
    spec = spec or split_spec_from_config()
    # Ensure master index is tz-aware UTC (parquet round-trip can change resolution/tz).
    if master.index.tz is None:
        master = master.copy()
        master.index = pd.to_datetime(master.index, utc=True)

    # GRU samples one issue per step on the issue-time grid (every 6h to match other models).
    issue_grid = pd.DatetimeIndex(pd.to_datetime(fm.issue_time.unique(), utc=True)).sort_values()
    train_issue = issue_grid[issue_grid < spec.val_start]
    val_issue = issue_grid[(issue_grid >= spec.val_start) & (issue_grid < spec.test_start)]
    test_issue = issue_grid[issue_grid >= spec.test_start]
    audit_issue = issue_grid[(issue_grid >= spec.audit_start) & (issue_grid <= spec.audit_end)]

    # Remove audit from training set.
    train_issue = train_issue[~train_issue.isin(audit_issue)]

    from lake_forecast.features.lags import last_valid_water_temp

    anchor = last_valid_water_temp(master["water_temp"], max_lookback_hours=24)

    print(
        f"GRU split sizes  train={len(train_issue)} val={len(val_issue)} "
        f"test={len(test_issue)} audit={len(audit_issue)}"
    )

    model = GRUForecaster()
    model.fit_from_master(master, train_issue, val_issue, anchor, day_mask_fn=daytime_mask)

    te = evaluate_gru_on_master(model, master, anchor, test_issue)
    au = evaluate_gru_on_master(model, master, anchor, audit_issue)
    if te is None:
        return None
    print(f"  test MAE={te.mae:.4f}  audit MAE={(au.mae if au else float('nan')):.4f}")

    result = TrainResult(
        name="gru",
        val_mae=None,
        test_mae=te.mae,
        audit_mae=(au.mae if au else float("nan")),
        test_skill=te.skill_vs_persistence,
        audit_skill=(au.skill_vs_persistence if au else None),
        per_horizon_test=te.per_horizon.to_dict(orient="records"),
        per_horizon_audit=(au.per_horizon.to_dict(orient="records") if au else []),
        per_month_test=te.per_month.to_dict(orient="records"),
    )

    metrics_path = repo_path(metrics_dir)
    metrics_path.mkdir(parents=True, exist_ok=True)
    payload = {"gru": asdict(result)}
    with open(metrics_path / "gru.json", "w") as fh:
        json.dump(payload, fh, indent=2)
    artifacts_path = repo_path(artifacts_dir)
    artifacts_path.mkdir(parents=True, exist_ok=True)
    import torch

    torch.save(
        {
            "state_dict": model.model.state_dict() if model.model else {},
            "cfg": asdict(model.cfg),
            "enc_mean": model.enc_mean,
            "enc_std": model.enc_std,
            "dec_mean": model.dec_mean,
            "dec_std": model.dec_std,
            "enc_feat_names": model.enc_feat_names,
            "dec_feat_names": model.dec_feat_names,
        },
        artifacts_path / "gru.pt",
    )

    return result
