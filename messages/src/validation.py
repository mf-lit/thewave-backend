"""The one exception the HTTP layer turns into a 400.

It lives in its own module rather than in ``models`` because ``markdown``
raises it too and ``models`` calls ``markdown`` — importing it the other way
round would be a cycle.

The module is named ``validation`` and not ``errors`` for the same reason
``api/handlers.py`` is not ``api/errors.py``: log-alerts greps container output
for ``error|exception|fatal|panic`` and pages a phone on a match, so a logger
named ``src.errors`` would turn every client-side typo into a 3am alert.
"""
from __future__ import annotations


class ValidationError(ValueError):
    """A client-visible validation failure. ``str(exc)`` is the API message."""
