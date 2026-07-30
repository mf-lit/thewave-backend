"""Flask application factory.

Importing this module has no side effects — the process-wide instance lives in
``src/wsgi.py`` — so tests can build an app against a temporary database and a
fake calendar without touching the real ones.
"""
from __future__ import annotations

import logging
from typing import Optional

from flask import Flask

from ..services import Services
from ..settings import Settings
from . import handlers
from .routes import bp

logger = logging.getLogger(__name__)


def _check_auth_configured(services: Services) -> None:
    """Refuse to serve with authentication silently unconfigured."""
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


def create_app(services: Optional[Services] = None, settings: Optional[Settings] = None) -> Flask:
    """Build the WSGI app. Pass ``services`` to inject fakes in tests."""
    services = services or Services.build(settings)
    _check_auth_configured(services)

    app = Flask(__name__)
    app.extensions["notifications"] = services
    app.register_blueprint(bp)
    handlers.register(app)
    return app
