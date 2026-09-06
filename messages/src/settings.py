"""Runtime configuration.

Environment variables are read once per process into a frozen ``Settings``.
The YAML config file is read lazily and re-read when its mtime changes, so
keys can be rotated by editing the mounted file without a restart.

Two key lists live in that one file. ``api_keys`` reaches ``/messages`` and
ships inside the app; ``admin_keys`` reaches ``/admin/*``, which authors and
retracts messages, and never leaves this box.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_DB_PATH = "data/messages.db"
DEFAULT_CONFIG_PATH = "config/config.yaml"
DEFAULT_UPSTREAM_DB_PATH = "/data/upstream-api/water_temperature.db"

# How long a client may hold its message list before asking again. Served in
# the `ttl` field rather than a Cache-Control max-age: the response is
# per-client and must never be cached by anything in front of us.
DEFAULT_TTL_SECONDS = 900


def _resolve(value: str) -> Path:
    """Resolve a possibly-relative path against the project root."""
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").lower() in ("1", "true", "yes")


class ConfigFile:
    """The mounted ``config.yaml``, cached until its mtime changes."""

    def __init__(self, path: Path):
        self.path = path
        self._cached: dict = {}
        self._mtime: Optional[float] = None

    def _load(self) -> dict:
        try:
            mtime = self.path.stat().st_mtime
        except OSError:
            self._cached, self._mtime = {}, None
            return self._cached

        if mtime != self._mtime:
            try:
                with self.path.open() as handle:
                    self._cached = yaml.safe_load(handle) or {}
            except (OSError, yaml.YAMLError) as exc:
                logger.warning("Could not read config file %s: %s", self.path, exc)
                self._cached = {}
            self._mtime = mtime
        return self._cached

    def _keys(self, field: str) -> List[str]:
        keys = self._load().get(field) or []
        return [key for key in keys if key]

    def api_keys(self) -> List[str]:
        return self._keys("api_keys")

    def admin_keys(self) -> List[str]:
        return self._keys("admin_keys")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    config_path: Path
    upstream_db_path: Path
    auth_disabled: bool
    ttl_seconds: int

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=_resolve(os.getenv("SQLITE_DB_PATH", DEFAULT_DB_PATH)),
            config_path=_resolve(os.getenv("MESSAGES_CONFIG_PATH", DEFAULT_CONFIG_PATH)),
            upstream_db_path=_resolve(
                os.getenv("UPSTREAM_DB_PATH", DEFAULT_UPSTREAM_DB_PATH)
            ),
            auth_disabled=_env_flag("DISABLE_API_AUTH"),
            ttl_seconds=int(os.getenv("MESSAGES_TTL_SECONDS", DEFAULT_TTL_SECONDS)),
        )

    def config_file(self) -> ConfigFile:
        return ConfigFile(self.config_path)


def configure_logging() -> None:
    """Match the log format the other containers emit."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
