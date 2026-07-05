"""LightGBM model with L1 objective; supports Optuna hyperparameter search."""

from __future__ import annotations

from typing import Any

import lightgbm as lgb
import numpy as np
import optuna
import pandas as pd

from lake_forecast.models.base import Forecaster


class LightGBMModel(Forecaster):
    name = "lightgbm"

    def __init__(self, params: dict[str, Any] | None = None, n_estimators: int = 4000):
        self.params = params or {}
        self.n_estimators = n_estimators
        self.booster: lgb.Booster | None = None

    def fit(
        self,
        X: pd.DataFrame,
        y: np.ndarray,
        *,
        sample_weight: np.ndarray | None = None,
        eval_set: tuple[pd.DataFrame, np.ndarray] | None = None,
        early_stopping_rounds: int | None = 200,
        verbose: bool = False,
    ) -> LightGBMModel:
        y = np.asarray(y, dtype=float)
        m = np.isfinite(y)
        Xt = X.iloc[m]
        yt = y[m]
        sw = None if sample_weight is None else np.asarray(sample_weight, dtype=float)[m]

        params = {
            "objective": "regression_l1",
            "metric": "mae",
            "verbosity": -1,
            "force_row_wise": True,
            "feature_fraction_seed": 42,
            "bagging_seed": 42,
            "deterministic": True,
            **self.params,
        }
        train_set = lgb.Dataset(Xt, label=yt, weight=sw)
        valid_sets = [train_set]
        valid_names = ["train"]
        callbacks: list[Any] = []
        if eval_set is not None:
            Xv, yv = eval_set
            yv = np.asarray(yv, dtype=float)
            mv = np.isfinite(yv)
            valid_sets.append(lgb.Dataset(Xv.iloc[mv], label=yv[mv]))
            valid_names.append("val")
            if early_stopping_rounds:
                callbacks.append(lgb.early_stopping(early_stopping_rounds, verbose=verbose))
        if verbose:
            callbacks.append(lgb.log_evaluation(100))
        else:
            callbacks.append(lgb.log_evaluation(0))

        self.booster = lgb.train(
            params,
            train_set,
            num_boost_round=self.n_estimators,
            valid_sets=valid_sets,
            valid_names=valid_names,
            callbacks=callbacks,
        )
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.booster is None:
            raise RuntimeError("LightGBMModel not fit yet")
        return self.booster.predict(X, num_iteration=self.booster.best_iteration or None)


def optuna_search(
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_val: pd.DataFrame,
    y_val: np.ndarray,
    sample_weight_train: np.ndarray | None,
    sample_weight_val: np.ndarray | None,
    n_trials: int = 80,
    timeout_seconds: int | None = None,
    seed: int = 42,
) -> dict[str, Any]:
    """Run Optuna search per the plan's HP grid. Returns the best param dict."""

    def objective(trial: optuna.Trial) -> float:
        params = {
            "num_leaves": trial.suggest_int("num_leaves", 15, 255),
            "learning_rate": trial.suggest_float("learning_rate", 0.01, 0.1, log=True),
            "min_data_in_leaf": trial.suggest_int("min_data_in_leaf", 20, 500),
            "feature_fraction": trial.suggest_float("feature_fraction", 0.6, 1.0),
            "bagging_fraction": trial.suggest_float("bagging_fraction", 0.6, 1.0),
            "bagging_freq": 1,
            "lambda_l1": trial.suggest_float("lambda_l1", 1e-8, 10.0, log=True),
            "lambda_l2": trial.suggest_float("lambda_l2", 1e-8, 10.0, log=True),
        }
        model = LightGBMModel(params=params, n_estimators=4000)
        model.fit(
            X_train,
            y_train,
            sample_weight=sample_weight_train,
            eval_set=(X_val, y_val),
            early_stopping_rounds=200,
        )
        preds = model.predict(X_val)
        # MAE on rows where val target is finite AND the val sample_weight is non-zero
        yv = np.asarray(y_val, dtype=float)
        m = np.isfinite(yv)
        if sample_weight_val is not None:
            sw = np.asarray(sample_weight_val, dtype=float)
            m &= sw > 0
        return float(np.mean(np.abs(preds[m] - yv[m])))

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="minimize", sampler=sampler)
    study.optimize(objective, n_trials=n_trials, timeout=timeout_seconds, show_progress_bar=False)
    return study.best_params
