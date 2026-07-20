"""Standardized linear model with Huber loss (MAE-friendly)."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import HuberRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from lake_forecast.models.base import Forecaster


class HuberLinearModel(Forecaster):
    name = "huber_linear"

    def __init__(self, alpha: float = 1.0, epsilon: float = 1.35, max_iter: int = 500) -> None:
        self.alpha = alpha
        self.epsilon = epsilon
        self.max_iter = max_iter
        self.pipe: Pipeline | None = None

    def fit(
        self,
        X: pd.DataFrame,
        y: np.ndarray,
        *,
        sample_weight: np.ndarray | None = None,
        eval_set=None,
    ) -> HuberLinearModel:
        # HuberRegressor doesn't accept NaN; drop rows here.
        y = np.asarray(y, dtype=float)
        m = np.isfinite(y) & np.all(np.isfinite(X.values), axis=1)
        if sample_weight is not None:
            sample_weight = np.asarray(sample_weight, dtype=float)[m]
        self.pipe = Pipeline(
            [
                ("scaler", StandardScaler(with_mean=True, with_std=True)),
                ("huber", HuberRegressor(alpha=self.alpha, epsilon=self.epsilon, max_iter=self.max_iter)),
            ]
        )
        self.pipe.fit(X.iloc[m], y[m], huber__sample_weight=sample_weight)
        return self

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        if self.pipe is None:
            raise RuntimeError("HuberLinearModel not fit yet")
        Xf = X.fillna(X.median(numeric_only=True))
        return self.pipe.predict(Xf)
