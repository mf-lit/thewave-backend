"""Split tests: summer-audit isolation, no train/val/test overlap, walk-forward."""

from __future__ import annotations

import pandas as pd

from lake_forecast.splits import SplitSpec, make_masks, walk_forward_folds


def _issue_times() -> pd.Series:
    idx = pd.date_range("2023-10-01", "2026-05-19", freq="6h", tz="UTC")
    return pd.Series(idx.values, name="issue_time")


def _spec() -> SplitSpec:
    return SplitSpec(
        test_start=pd.Timestamp("2026-02-19", tz="UTC"),
        val_start=pd.Timestamp("2025-11-21", tz="UTC"),
        audit_start=pd.Timestamp("2024-06-01", tz="UTC"),
        audit_end=pd.Timestamp("2024-08-31 23:00", tz="UTC"),
    )


def test_no_overlap_and_audit_excluded_from_others() -> None:
    issue = _issue_times()
    masks = make_masks(issue, _spec())
    # Mutually exclusive
    s = (
        masks["train"].astype(int)
        + masks["val"].astype(int)
        + masks["test"].astype(int)
        + masks["audit"].astype(int)
    )
    assert (s == 1).all() or (s == 0).all() or (s <= 1).all()
    assert s.max() <= 1
    # Audit window is non-empty and never overlaps the other three
    assert masks["audit"].sum() > 0
    overlap_with_others = (masks["audit"] & (masks["train"] | masks["val"] | masks["test"])).sum()
    assert overlap_with_others == 0


def test_walk_forward_train_lt_val() -> None:
    issue = _issue_times()
    folds = walk_forward_folds(issue, n_folds=4, fold_days=30, spec=_spec())
    assert len(folds) == 4
    issue_dt = pd.to_datetime(issue, utc=True)
    for tr, va in folds:
        if tr.sum() == 0 or va.sum() == 0:
            continue
        # All training issue times must be strictly before any val issue time.
        assert issue_dt[tr].max() < issue_dt[va].min()
        # Audit window must be excluded from both halves.
        assert not ((issue_dt[tr] >= _spec().audit_start) & (issue_dt[tr] <= _spec().audit_end)).any()
        assert not ((issue_dt[va] >= _spec().audit_start) & (issue_dt[va] <= _spec().audit_end)).any()
