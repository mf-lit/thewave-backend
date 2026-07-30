import json
import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

UPGRADE_REQUIRED_TITLE = "App Upgrade Required"

# Global map of lowercased OS name -> minimum required version string, loaded at startup.
_MIN_CLIENT_VERSIONS: dict[str, str] = {}


def load_min_client_versions() -> dict[str, str]:
    """
    Load the minimum required client version per OS.

    Priority:
    1. MIN_CLIENT_VERSIONS environment variable (JSON object, e.g. {"ios": "2.3.0"})
    2. config.yaml file (min_client_versions field)
    3. Empty dict (gate disabled)

    Returns:
        dict[str, str]: Lowercased OS name -> minimum version string.
    """
    global _MIN_CLIENT_VERSIONS

    env_value = os.getenv("MIN_CLIENT_VERSIONS", "").strip()
    if env_value:
        try:
            parsed = json.loads(env_value)
            if isinstance(parsed, dict):
                _MIN_CLIENT_VERSIONS = {str(k).lower(): str(v) for k, v in parsed.items()}
                logger.info(f"Using min_client_versions from MIN_CLIENT_VERSIONS env var: {_MIN_CLIENT_VERSIONS}")
                return _MIN_CLIENT_VERSIONS
            logger.warning("MIN_CLIENT_VERSIONS env var is not a JSON object, ignoring")
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse MIN_CLIENT_VERSIONS env var: {e}, ignoring")

    project_root = Path(__file__).parent.parent.parent
    config_file = project_root / "config" / "config.yaml"

    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)

            if config and isinstance(config, dict):
                min_versions = config.get("min_client_versions")
                if isinstance(min_versions, dict):
                    _MIN_CLIENT_VERSIONS = {str(k).lower(): str(v) for k, v in min_versions.items()}
                    logger.info(f"Using min_client_versions from config.yaml: {_MIN_CLIENT_VERSIONS}")
                    return _MIN_CLIENT_VERSIONS
                elif min_versions is not None:
                    logger.warning("min_client_versions in config.yaml is not a mapping, ignoring")
        except yaml.YAMLError as e:
            logger.warning(f"Failed to load min_client_versions from config.yaml: {e}")

    _MIN_CLIENT_VERSIONS = {}
    logger.info("No min_client_versions configured; upgrade gate disabled")
    return _MIN_CLIENT_VERSIONS


def _parse_version(version: str) -> tuple[int, ...] | None:
    """Parse a dot-separated numeric version string, e.g. "2.3.0" -> (2, 3, 0).

    Returns None if any segment isn't a plain non-negative integer.
    """
    if not version:
        return None
    segments = version.split(".")
    parsed = []
    for segment in segments:
        if not segment.isdigit():
            return None
        parsed.append(int(segment))
    return tuple(parsed)


def is_upgrade_required(client_os: str | None, client_version: str | None) -> bool:
    """
    Determine whether a client's OS/version falls below the configured minimum.

    Fails open (returns False) whenever the OS isn't configured or either version
    string can't be parsed, so malformed input never triggers the nag.
    """
    if not client_os or not client_version:
        return False

    min_version_str = _MIN_CLIENT_VERSIONS.get(client_os.lower())
    if min_version_str is None:
        return False

    client_tuple = _parse_version(client_version)
    min_tuple = _parse_version(min_version_str)
    if client_tuple is None or min_tuple is None:
        return False

    length = max(len(client_tuple), len(min_tuple))
    client_padded = client_tuple + (0,) * (length - len(client_tuple))
    min_padded = min_tuple + (0,) * (length - len(min_tuple))
    return client_padded < min_padded


def apply_upgrade_gate(response_data: dict, client_os: str | None, client_version: str | None) -> dict:
    """
    Rewrite every performance's session title to "App Upgrade Required" when the
    requesting client's OS/version is below the configured minimum.

    Never mutates response_data in place: day dicts served from main.py's
    _day_cache are shared across every caller requesting that date, so an
    in-place rewrite here would leak the nag (or the override itself) into
    other clients' responses. Returns response_data unchanged when no gate
    applies, and a copy with new day/performance/fields dicts otherwise.

    Args:
        response_data: Calendar response data with a "days" list.
        client_os: Value of the X-Client-OS header, if any.
        client_version: Value of the X-Client-Version header, if any.

    Returns:
        dict: response_data, gated or not.
    """
    if not is_upgrade_required(client_os, client_version):
        return response_data

    days = response_data.get("days")
    if not isinstance(days, list):
        return response_data

    new_days = []
    for day in days:
        if not isinstance(day, dict) or not isinstance(day.get("performances"), list):
            new_days.append(day)
            continue

        new_performances = []
        for performance in day["performances"]:
            if not isinstance(performance, dict) or not isinstance(performance.get("fields"), dict):
                new_performances.append(performance)
                continue

            new_fields = {**performance["fields"], "title": UPGRADE_REQUIRED_TITLE}
            new_performances.append({**performance, "fields": new_fields})

        new_days.append({**day, "performances": new_performances})

    return {**response_data, "days": new_days}
