"""Build the cleaned master table and feature matrix for training."""

from __future__ import annotations

from lake_forecast.config import data_config, repo_path
from lake_forecast.data.align import build_master_table
from lake_forecast.features.build import build_training_matrix, save_feature_matrix


def main() -> None:
    cfg = data_config()
    master = build_master_table()
    print(f"master rows={len(master)} cols={len(master.columns)}")
    fm = build_training_matrix(master)
    print(f"feature matrix rows={len(fm.X)} cols={len(fm.feature_columns)}")
    print(f"  target non-null: {fm.y.notna().sum()}  null: {fm.y.isna().sum()}")
    out = cfg["paths"]["feature_matrix_parquet"]
    save_feature_matrix(fm, out)
    print(f"saved → {repo_path(out)}")


if __name__ == "__main__":
    main()
