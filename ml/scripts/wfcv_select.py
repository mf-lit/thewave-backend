"""4-fold walk-forward CV with already-tuned hyperparameters.

For each candidate (huber best alpha, lightgbm best Optuna params), fit on the
expanding training window of each fold and report fold-mean masked MAE.
Promotes the CV winner to ``models/best/``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import joblib
import numpy as np

import pandas as pd

from lake_forecast.config import repo_path, train_config
from lake_forecast.eval import daytime_mask, evaluate
from lake_forecast.features.build import load_feature_matrix
from lake_forecast.models.lightgbm_model import LightGBMModel
from lake_forecast.models.linear import HuberLinearModel
from lake_forecast.splits import make_masks, walk_forward_folds


def _maybe(arr, idx):
    return arr.iloc[idx] if hasattr(arr, "iloc") else arr[idx]


def _eval_mae(fm, idx_va, preds) -> float:
    y_true = fm.y.iloc[idx_va].to_numpy(float)
    persistence = fm.X["water_temp_t0"].iloc[idx_va].to_numpy(float)
    res = evaluate(
        y_true=y_true,
        y_pred=preds,
        horizon=fm.horizon.iloc[idx_va].to_numpy(int),
        target_time=fm.target_time.iloc[idx_va],
        persistence_pred=persistence,
    )
    return res.mae


def main() -> None:
    fm = load_feature_matrix("data/processed/feature_matrix.parquet")
    masks = make_masks(fm.issue_time)
    folds = walk_forward_folds(fm.issue_time)
    print(f"folds: {len(folds)}")

    huber_alpha = float(train_config()["models"]["ridge"]["alphas"][0])
    lgbm_params = json.loads((repo_path("models") / "lightgbm_best_params.json").read_text())

    huber_maes: list[float] = []
    lgbm_maes: list[float] = []

    for i, (tr_mask, va_mask) in enumerate(folds, 1):
        tr_idx = np.where(tr_mask)[0]
        va_idx = np.where(va_mask)[0]
        sw_tr = daytime_mask(fm.target_time.iloc[tr_idx]).astype(float)
        # huber
        mdl = HuberLinearModel(alpha=huber_alpha)
        mdl.fit(fm.X.iloc[tr_idx], fm.y.iloc[tr_idx].to_numpy(float), sample_weight=sw_tr)
        huber_mae = _eval_mae(fm, va_idx, mdl.predict(fm.X.iloc[va_idx]))
        huber_maes.append(huber_mae)

        # lightgbm
        # Use small early-stopping window: tail 5% of train as monitor
        n_tr = len(tr_idx)
        monitor = tr_idx[int(0.95 * n_tr) :]
        actual_tr = tr_idx[: int(0.95 * n_tr)]
        sw_actual_tr = sw_tr[: int(0.95 * n_tr)]
        lgbm = LightGBMModel(params=lgbm_params, n_estimators=2000)
        lgbm.fit(
            fm.X.iloc[actual_tr],
            fm.y.iloc[actual_tr].to_numpy(float),
            sample_weight=sw_actual_tr,
            eval_set=(fm.X.iloc[monitor], fm.y.iloc[monitor].to_numpy(float)),
            early_stopping_rounds=100,
        )
        lgbm_mae = _eval_mae(fm, va_idx, lgbm.predict(fm.X.iloc[va_idx]))
        lgbm_maes.append(lgbm_mae)
        print(f"  fold {i}: huber={huber_mae:.4f}  lgbm={lgbm_mae:.4f}")

    huber_cv = float(np.mean(huber_maes))
    lgbm_cv = float(np.mean(lgbm_maes))
    print()
    print(f"huber  CV-mean MAE = {huber_cv:.4f}   (fold MAEs: {[round(x,4) for x in huber_maes]})")
    print(f"lgbm   CV-mean MAE = {lgbm_cv:.4f}   (fold MAEs: {[round(x,4) for x in lgbm_maes]})")

    best_name = "huber_linear" if huber_cv <= lgbm_cv else "lightgbm"
    print(f"\nWFCV winner: {best_name}")

    # Promote to models/best
    metrics_path = repo_path("reports/metrics/all_models.json")
    payload = json.loads(metrics_path.read_text())
    payload["wfcv"] = {
        "huber_linear_cv_mae": huber_cv,
        "lightgbm_cv_mae": lgbm_cv,
        "huber_linear_folds": huber_maes,
        "lightgbm_folds": lgbm_maes,
    }
    metrics_path.write_text(json.dumps(payload, indent=2))

    best_dir = repo_path("models/best")
    best_dir.mkdir(parents=True, exist_ok=True)
    src = repo_path(f"models/{best_name}.joblib")
    joblib.dump(joblib.load(src), best_dir / "model.joblib")
    (best_dir / "metadata.json").write_text(
        json.dumps(
            {
                "model": best_name,
                "selection_rule": "4-fold walk-forward CV mean MAE",
                "feature_columns": list(fm.X.columns),
                "huber_linear_cv_mae": huber_cv,
                "lightgbm_cv_mae": lgbm_cv,
                "test_mae": payload["results"][best_name]["test_mae"],
                "audit_mae": payload["results"][best_name]["audit_mae"],
                "trained_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
