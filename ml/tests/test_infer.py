"""Infer tests: state file lifecycle (refuses stale state)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


def test_update_and_read_state(tmp_path: Path) -> None:
    from lake_forecast.infer import _read_state, update_state_file

    p = tmp_path / "state.json"
    update_state_file(observed=14.5, state_path=p)
    state = _read_state(p, max_age_hours=24)
    assert state["last_water_temp"] == 14.5


def test_stale_state_rejected(tmp_path: Path) -> None:
    from lake_forecast.infer import _read_state, update_state_file

    p = tmp_path / "state.json"
    # Backdate the obs by 48h
    old = datetime.now(UTC) - timedelta(hours=48)
    update_state_file(observed=14.5, state_path=p, now=old)
    with pytest.raises(RuntimeError, match=r"state is .* old"):
        _read_state(p, max_age_hours=24)


def test_missing_state_file(tmp_path: Path) -> None:
    from lake_forecast.infer import _read_state

    p = tmp_path / "no_state.json"
    with pytest.raises(FileNotFoundError):
        _read_state(p)
