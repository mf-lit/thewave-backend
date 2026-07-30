"""API key authentication.

Keys live in the mounted ``config.yaml``. ``ConfigFile`` re-reads it only when
its mtime changes, so rotation still takes effect without a restart but a
burst of requests no longer re-parses YAML once per request.
"""
from __future__ import annotations

import hmac
import logging
from functools import wraps
from typing import Callable

from flask import current_app, request

logger = logging.getLogger(__name__)

MISSING_KEY_ERROR = "Missing x-api-key header"
INVALID_KEY_ERROR = "Invalid API key"


def _services():
    return current_app.extensions["notifications"]


def is_valid_key(candidate: str, valid_keys) -> bool:
    """Constant-time membership test, so timing can't reveal a key."""
    return any(hmac.compare_digest(candidate, key) for key in valid_keys)


def require_api_key(view: Callable) -> Callable:
    """Reject requests without a recognised ``x-api-key`` header."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        services = _services()
        if services.settings.auth_disabled:
            return view(*args, **kwargs)

        provided = request.headers.get("x-api-key")
        if not provided:
            logger.info("Rejected %s %s: no API key", request.method, request.path)
            return {"error": MISSING_KEY_ERROR}, 401

        if not is_valid_key(provided, services.config.api_keys()):
            logger.info("Rejected %s %s: unrecognised API key", request.method, request.path)
            return {"error": INVALID_KEY_ERROR}, 401

        return view(*args, **kwargs)

    return wrapper
