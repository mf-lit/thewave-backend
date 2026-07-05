"""Refit the LightGBM model with saved best params and corrected refit logic.

Skips Optuna; uses ``models/lightgbm_best_params.json`` from a prior run.
Updates ``models/lightgbm.joblib``, ``models/best/{model.joblib, metadata.json}``,
and ``reports/metrics/all_models.json`` (only the lightgbm entry).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime

import joblib
import numpy as np

from lake_forecast.config import repo_path
from lake_forecast.features.build import load_feature_matrix
from lake_forecast.models.lightgbm_model import LightGBMModel
from lake_forecast.splits import make_masks
from lake_forecast.train import _make_eval, _sample_weights_daytime, TrainResult


def main() -> None:
    fm = load_feature_matrix("data/processed/feature_matrix.parquet")
    masks = make_masks(fm.issue_time)
    tr_idx = np.where(masks["train"])[0]
    va_idx = np.where(masks["val"])[0]
    te_idx = np.where(masks["test"])[0]
    au_idx = np.where(masks["audit"])[0]
    X, y = fm.X, fm.y.to_numpy(float)

    best_params = json.loads((repo_path("models") / "lightgbm_best_params.json").read_text())
    print(f"using best params: {best_params}")

    sw_tr = _sample_weights_daytime(fm, tr_idx)
    mdl = LightGBMModel(params=best_params, n_estimators=4000)
    mdl.fit(
        X.iloc[tr_idx],
        y[tr_idx],
        sample_weight=sw_tr,
        eval_set=(X.iloc[va_idx], y[va_idx]),
        early_stopping_rounds=200,
        verbose=False,
    )
    joblib.dump(mdl, repo_path("models/lightgbm.joblib"))

    va = _make_eval(fm, masks["val"], mdl.predict(X.iloc[va_idx]))
    te = _make_eval(fm, masks["test"], mdl.predict(X.iloc[te_idx]))
    au = _make_eval(fm, masks["audit"], mdl.predict(X.iloc[au_idx]))
    print(f"val MAE={va.mae:.4f}  test MAE={te.mae:.4f}  audit MAE={au.mae:.4f}")

    result = TrainResult(
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

    # Merge into all_models.json
    metrics_path = repo_path("reports/metrics/all_models.json")
    payload = json.loads(metrics_path.read_text())
    payload["results"]["lightgbm"] = asdict(result)
    payload["trained_at"] = datetime.now(UTC).isoformat()
    metrics_path.write_text(json.dumps(payload, indent=2))

    # Re-select best by val MAE across tabular candidates.
    candidates = {
        k: v for k, v in payload["results"].items()
        if k in ("huber_linear", "lightgbm") and v.get("val_mae") is not None
    }
    best_name = min(candidates, key=lambda k: candidates[k]["val_mae"])
    print(f"best (by val MAE): {best_name}  val={candidates[best_name]['val_mae']:.4f}")

    best_dir = repo_path("models/best")
    best_dir.mkdir(parents=True, exist_ok=True)
    src = repo_path(f"models/{best_name}.joblib")
    joblib.dump(joblib.load(src), best_dir / "model.joblib")
    (best_dir / "metadata.json").write_text(
        json.dumps(
            {
                "model": best_name,
                "feature_columns": list(X.columns),
                "val_mae": candidates[best_name]["val_mae"],
                "test_mae": candidates[best_name]["test_mae"],
                "audit_mae": candidates[best_name]["audit_mae"],
                "trained_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
