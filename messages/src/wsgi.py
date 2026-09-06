"""WSGI entry point: ``gunicorn src.wsgi:app``."""
from __future__ import annotations

import logging
import sys

from .api.app import create_app
from .settings import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

try:
    app = create_app()
except Exception as exc:  # noqa: BLE001 - startup must fail loudly and exit
    logger.error("Startup failed: %s", exc)
    sys.exit(1)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5005)
