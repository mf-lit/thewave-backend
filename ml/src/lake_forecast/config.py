"""Typed loaders for the YAML configs under configs/."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "configs"


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open() as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def data_config() -> dict[str, Any]:
    return _load_yaml("data.yaml")


@lru_cache(maxsize=1)
def features_config() -> dict[str, Any]:
    return _load_yaml("features.yaml")


@lru_cache(maxsize=1)
def train_config() -> dict[str, Any]:
    return _load_yaml("train.yaml")


def repo_path(*parts: str) -> Path:
    return REPO_ROOT.joinpath(*parts)
