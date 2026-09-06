"""API key authentication.

Two independent key lists in the one mounted ``config.yaml``, checked by two
decorators. ``x-api-key`` reaches the client endpoints and ships inside the
app; ``x-admin-key`` reaches ``/admin/*``, which authors and retracts
messages, and exists only on this box.

They are separate headers rather than one list with a role, so a client key
leaking out of a decompiled app — which is a matter of when, not if — cannot
be tried against the admin surface.

``ConfigFile`` re-reads the file only when its mtime changes, so rotation
takes effect without a restart and a burst of requests does not re-parse YAML
once per request.
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
MISSING_ADMIN_KEY_ERROR = "Missing x-admin-key header"
INVALID_ADMIN_KEY_ERROR = "Invalid admin key"


def _services():
    return current_app.extensions["messages"]


def is_valid_key(candidate: str, valid_keys) -> bool:
    """Constant-time membership test, so timing can't reveal a key."""
    return any(hmac.compare_digest(candidate, key) for key in valid_keys)


def _guard(header: str, keys, missing: str, invalid: str, honour_disable: bool):
    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapper(*args, **kwargs):
            services = _services()
            if honour_disable and services.settings.auth_disabled:
                return view(*args, **kwargs)

            provided = request.headers.get(header)
            if not provided:
                logger.info("Rejected %s %s: no key", request.method, request.path)
                return {"error": missing}, 401

            if not is_valid_key(provided, keys(services.config)):
                logger.info(
                    "Rejected %s %s: unrecognised key", request.method, request.path
                )
                return {"error": invalid}, 401

            return view(*args, **kwargs)

        return wrapper

    return decorator


require_api_key = _guard(
    "x-api-key",
    lambda config: config.api_keys(),
    MISSING_KEY_ERROR,
    INVALID_KEY_ERROR,
    honour_disable=True,
)

# DISABLE_API_AUTH is a local-development switch for the client endpoints and
# does not reach here. An admin surface that opens itself on an environment
# variable is one stray line in a compose file away from being public.
require_admin_key = _guard(
    "x-admin-key",
    lambda config: config.admin_keys(),
    MISSING_ADMIN_KEY_ERROR,
    INVALID_ADMIN_KEY_ERROR,
    honour_disable=False,
)
