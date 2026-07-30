"""Runtime configuration.

Environment variables are read once per process into a frozen ``Settings``.
The YAML config file is read lazily and re-read when its mtime changes, so
API keys can be rotated by editing the mounted file without a restart.
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

DEFAULT_DB_PATH = "data/notifications.db"
DEFAULT_CONFIG_PATH = "config/config.yaml"
DEFAULT_CREDENTIALS_PATH = "config/google_application_credentials.json"
DEFAULT_CALENDAR_API_URL = "http://localhost:5000/calendar"
DEFAULT_HEARTBEAT_PATH = "data/worker-heartbeat"


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

    def api_keys(self) -> List[str]:
        keys = self._load().get("api_keys") or []
        return [key for key in keys if key]

    def calendar_api_key(self) -> Optional[str]:
        return self._load().get("calendar_api_key")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    config_path: Path
    credentials_path: Path
    calendar_api_url: str
    calendar_api_key_env: Optional[str]
    auth_disabled: bool
    heartbeat_path: Path

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=_resolve(os.getenv("SQLITE_DB_PATH", DEFAULT_DB_PATH)),
            config_path=_resolve(os.getenv("NOTIFICATIONS_CONFIG_PATH", DEFAULT_CONFIG_PATH)),
            credentials_path=_resolve(
                os.getenv("GOOGLE_APPLICATION_CREDENTIALS", DEFAULT_CREDENTIALS_PATH)
            ),
            calendar_api_url=os.getenv("CALENDAR_API_URL", DEFAULT_CALENDAR_API_URL),
            calendar_api_key_env=os.getenv("CALENDAR_API_KEY") or None,
            auth_disabled=_env_flag("DISABLE_API_AUTH"),
            heartbeat_path=_resolve(os.getenv("WORKER_HEARTBEAT_PATH", DEFAULT_HEARTBEAT_PATH)),
        )

    def config_file(self) -> ConfigFile:
        return ConfigFile(self.config_path)


def configure_logging() -> None:
    """Match the log format the containers have always emitted."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
