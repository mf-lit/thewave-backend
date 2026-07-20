"""Forecaster ABC shared across model families."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
import pandas as pd


class Forecaster(ABC):
    name: str = "abstract"

    @abstractmethod
    def fit(
        self,
        X: pd.DataFrame,
        y: np.ndarray,
        *,
        sample_weight: np.ndarray | None = None,
        eval_set: tuple[pd.DataFrame, np.ndarray] | None = None,
    ) -> Forecaster: ...

    @abstractmethod
    def predict(self, X: pd.DataFrame) -> np.ndarray: ...

    def save(self, path: Path) -> None:  # pragma: no cover
        raise NotImplementedError

    @classmethod
    def load(cls, path: Path) -> Forecaster:  # pragma: no cover
        raise NotImplementedError
