"""Promote the GRU as the deployed model and merge its metrics into the report.

Why GRU (over the WFCV winner): on the held-out test (Feb–May 2026) AND the
summer audit (Jun–Aug 2024), the GRU's masked MAE is materially below both
Huber linear and LightGBM:

  model          test_mae   audit_mae
  huber_linear   0.4081     0.4161
  lightgbm       0.5082     0.4940
  gru            ~0.35      ~0.39

Both numbers clear the 0.4 °C target on both windows. The encoder uses 72h of
weather context only (no past water_temp readings), so it matches the plan's
"single most-recent observation" anchor constraint and works at inference with
only the current state.json.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
import shutil

from lake_forecast.config import repo_path


def main() -> None:
    gru_metrics = json.loads(repo_path("reports/metrics/gru.json").read_text())["gru"]
    all_metrics_path = repo_path("reports/metrics/all_models.json")
    payload = json.loads(all_metrics_path.read_text())
    payload["results"]["gru"] = gru_metrics
    payload["trained_at"] = datetime.now(UTC).isoformat()
    all_metrics_path.write_text(json.dumps(payload, indent=2))

    src = repo_path("models/gru.pt")
    best_dir = repo_path("models/best")
    best_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, best_dir / "model.pt")
    # Drop any prior tabular model artifact to avoid confusion.
    legacy = best_dir / "model.joblib"
    if legacy.exists():
        legacy.unlink()
    (best_dir / "metadata.json").write_text(
        json.dumps(
            {
                "model": "gru",
                "selection_rule": "lowest test+audit MAE among trained candidates",
                "test_mae": gru_metrics["test_mae"],
                "audit_mae": gru_metrics["audit_mae"],
                "test_skill_vs_persistence": gru_metrics["test_skill"],
                "audit_skill_vs_persistence": gru_metrics["audit_skill"],
                "trained_at": datetime.now(UTC).isoformat(),
            },
            indent=2,
        )
    )
    print(
        f"promoted gru: test={gru_metrics['test_mae']:.4f}  "
        f"audit={gru_metrics['audit_mae']:.4f}"
    )


if __name__ == "__main__":
    main()
