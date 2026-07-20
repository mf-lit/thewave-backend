"""Proxy health checking — reads the health status file written by the VPN health monitor."""

import logging
import os
import time

logger = logging.getLogger(__name__)

PROXY_HEALTH_FILE = os.environ.get("PROXY_HEALTH_FILE", "/app/shared/proxy_health")
_MAX_HEALTH_AGE = 30  # seconds — status older than this is treated as unknown


def wait_for_healthy_proxy(timeout: float = 30) -> bool:
    """Block until the proxy health file reports healthy or timeout is reached.

    Returns True if proxy is healthy, False on timeout.  If the health file
    doesn't exist (e.g. first deploy before health check writes it) we return
    True immediately so requests aren't blocked.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(PROXY_HEALTH_FILE, "r", encoding="utf-8") as f:
                content = f.read().strip()
        except FileNotFoundError:
            # Health file doesn't exist yet — assume healthy (backward compat)
            return True

        parts = content.split("|")
        if parts[0] == "healthy":
            try:
                age = time.time() - int(parts[1])
                if age < _MAX_HEALTH_AGE:
                    return True
            except (IndexError, ValueError):
                return True  # malformed but marked healthy — proceed
        # Proxy unhealthy or stale — wait and retry
        logger.debug(f"Proxy unhealthy, waiting... ({content})")
        time.sleep(2)

    logger.warning(f"Proxy still unhealthy after {timeout}s timeout")
    return False
