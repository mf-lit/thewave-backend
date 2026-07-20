"""Chronological splits, summer audit isolation, and walk-forward CV folds.

All split boundaries operate on **issue_time** (UTC). The summer-audit window
is removed from training entirely and never used for model selection.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from lake_forecast.config import train_config


@dataclass(frozen=True)
class SplitSpec:
    test_start: pd.Timestamp
    val_start: pd.Timestamp
    audit_start: pd.Timestamp
    audit_end: pd.Timestamp  # inclusive


def _ts(s: str | datetime) -> pd.Timestamp:
    if isinstance(s, pd.Timestamp):
        return s.tz_convert("UTC") if s.tzinfo else s.tz_localize("UTC")
    ts = pd.Timestamp(s)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def split_spec_from_config() -> SplitSpec:
    cfg = train_config()["splits"]
    return SplitSpec(
        test_start=_ts(cfg["test_start"]),
        val_start=_ts(cfg["val_start"]),
        audit_start=_ts(cfg["summer_audit_start"]),
        audit_end=_ts(cfg["summer_audit_end"]) + pd.Timedelta(hours=23),
    )


def rolling_split_spec(
    latest_issue: pd.Timestamp,
    test_days: int,
    val_days: int,
) -> SplitSpec:
    """Build a SplitSpec whose test/val windows roll back from ``latest_issue``.

    Used by the periodic retrain so each run trains on all data older than the
    val window and evaluates on the most recent ``test_days``. The summer-audit
    window stays pinned (from config) as a stable cross-run benchmark.
    """
    cfg = train_config()["splits"]
    latest = _ts(latest_issue).floor("D")
    test_start = latest - pd.Timedelta(days=test_days)
    val_start = test_start - pd.Timedelta(days=val_days)
    return SplitSpec(
        test_start=test_start,
        val_start=val_start,
        audit_start=_ts(cfg["summer_audit_start"]),
        audit_end=_ts(cfg["summer_audit_end"]) + pd.Timedelta(hours=23),
    )


def make_masks(issue_time: pd.Series, spec: SplitSpec | None = None) -> dict[str, np.ndarray]:
    """Return boolean masks (train/val/test/audit) over the rows of issue_time.

    The audit window is removed from train. Rows in [audit_start, audit_end]
    are flagged 'audit' and are NEVER in train/val/test.
    """
    spec = spec or split_spec_from_config()
    issue = pd.to_datetime(issue_time, utc=True)

    audit = (issue >= spec.audit_start) & (issue <= spec.audit_end)
    test = (issue >= spec.test_start) & ~audit
    val = (issue >= spec.val_start) & (issue < spec.test_start) & ~audit
    train = (issue < spec.val_start) & ~audit

    return {
        "train": train.to_numpy(),
        "val": val.to_numpy(),
        "test": test.to_numpy(),
        "audit": audit.to_numpy(),
    }


def walk_forward_folds(
    issue_time: pd.Series,
    n_folds: int | None = None,
    fold_days: int | None = None,
    spec: SplitSpec | None = None,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window walk-forward CV over the train+val span (excludes audit and test).

    Yields ``(train_mask, val_mask)`` pairs. Each val window is the
    ``fold_days``-day block ending at ``spec.test_start - k * fold_days``.
    """
    cfg = train_config()["splits"]
    n_folds = int(n_folds or cfg["cv_folds"])
    fold_days = int(fold_days or cfg["cv_fold_days"])
    spec = spec or split_spec_from_config()
    issue = pd.to_datetime(issue_time, utc=True)

    audit = (issue >= spec.audit_start) & (issue <= spec.audit_end)

    folds: list[tuple[np.ndarray, np.ndarray]] = []
    val_end = spec.test_start
    for _ in range(n_folds):
        val_start = val_end - pd.Timedelta(days=fold_days)
        train_mask = (issue < val_start) & ~audit
        val_mask = (issue >= val_start) & (issue < val_end) & ~audit
        folds.append((train_mask.to_numpy(), val_mask.to_numpy()))
        val_end = val_start
    folds.reverse()
    return folds
