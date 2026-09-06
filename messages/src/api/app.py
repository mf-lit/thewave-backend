"""Flask application factory.

Importing this module has no side effects — the process-wide instance lives in
``src/wsgi.py`` — so tests can build an app against a temporary database
without touching the real one.
"""
from __future__ import annotations

import logging
from typing import Optional

from flask import Flask

from ..services import Services
from ..settings import Settings
from . import handlers
from .routes import admin_bp, bp

logger = logging.getLogger(__name__)


def _check_auth_configured(services: Services) -> None:
    """Refuse to serve with client authentication silently unconfigured."""
    if services.settings.auth_disabled:
        logger.warning("API authentication is DISABLED via DISABLE_API_AUTH")
        return

    keys = services.config.api_keys()
    if not keys:
        raise RuntimeError(
            f"No api_keys found in {services.settings.config_path}. "
            "Add at least one key, or set DISABLE_API_AUTH=1 to run without auth."
        )
    logger.info("Loaded %d API key(s)", len(keys))


def admin_enabled(services: Services) -> bool:
    """Whether the admin surface should exist in this process at all.

    Missing admin keys are not a startup failure the way missing client keys
    are — a deploy that can still serve messages to the app is worth having —
    but the routes are then never registered, so a misconfigured box returns
    404 rather than an open admin API.
    """
    if services.config.admin_keys():
        return True
    logger.warning(
        "No admin_keys found in %s; the /admin routes will not be served",
        services.settings.config_path,
    )
    return False


def create_app(services: Optional[Services] = None, settings: Optional[Settings] = None) -> Flask:
    """Build the WSGI app. Pass ``services`` to inject a test database."""
    services = services or Services.build(settings)
    _check_auth_configured(services)

    app = Flask(__name__)
    app.extensions["messages"] = services
    app.register_blueprint(bp)
    if admin_enabled(services):
        app.register_blueprint(admin_bp)
    handlers.register(app)
    return app
