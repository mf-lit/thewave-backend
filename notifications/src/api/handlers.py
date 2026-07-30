"""Error translation for the HTTP layer.

Every failure the client is allowed to see is raised as an ``ApiError`` or a
``ValidationError`` carrying the exact message. Anything else becomes a
generic 500: the previous implementation wrapped handlers in
``except Exception`` and returned ``str(exc)``, which handed callers upstream
hostnames and stack detail.

The module is deliberately not called ``errors``: log-alerts greps container
output for ``error|exception|fatal|panic`` and pages on a match, so a logger
named ``src.api.errors`` turned every client-side typo into a phone alert.
Rejections log at INFO with wording that does not match; genuine 500s go
through ``logger.exception`` and still alert.
"""
from __future__ import annotations

import logging

from werkzeug.exceptions import HTTPException

from ..models import ValidationError

logger = logging.getLogger(__name__)

INTERNAL_ERROR = "Internal server error"


class ApiError(Exception):
    """A client-visible failure with an explicit status code."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message = message
        self.status = status


def register(app) -> None:
    @app.errorhandler(ValidationError)
    def _validation(exc: ValidationError):
        logger.info("Rejected request: %s", exc)
        return {"error": str(exc)}, 400

    @app.errorhandler(ApiError)
    def _api_error(exc: ApiError):
        logger.info("Rejected request (%d): %s", exc.status, exc.message)
        return {"error": exc.message}, exc.status

    @app.errorhandler(HTTPException)
    def _http(exc: HTTPException):
        # Werkzeug's own responses (404 routing, 405, 415 on a missing JSON
        # content type) are part of the existing contract — pass them through.
        return exc

    @app.errorhandler(Exception)
    def _unexpected(exc: Exception):
        logger.exception("Unhandled exception serving request")
        return {"error": INTERNAL_ERROR}, 500
