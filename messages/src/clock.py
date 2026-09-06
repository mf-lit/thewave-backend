"""Time handling.

Everything stored and compared here is UTC, written **timezone-aware** —
``2026-09-06T10:20:30.123456+00:00``. That is the form
``upstream-api/src/core/client_tracker.py`` already writes, and this service
reads that file, so matching it keeps the two comparable without a conversion
step. It is deliberately *not* the naive form ``notifications/src/clock.py``
uses; that service predates the convention and cannot be changed without
rewriting rows the dashboard reads.

Wall-clock time does not appear in this service at all. The operator thinks in
Europe/London and the dashboard's compose form converts on submit, but nothing
below the HTTP layer ever sees a local time.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def utc_now() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """Timestamp for ``created_at`` / ``updated_at``.

    Aware UTC ISO-8601 with an explicit ``+00:00`` offset.
    """
    return utc_now().isoformat()


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored or client-supplied ISO-8601 stamp into an aware UTC datetime.

    Tolerant on the way in, canonical on the way out. Three forms are accepted:

    * ``…+00:00`` — what this service writes;
    * ``…Z`` — what a JSON client is most likely to send, and what
      ``fromisoformat`` refused before Python 3.11;
    * naive, with no offset at all — assumed UTC, which is what the operator
      means when the dashboard form omits one.

    Returns None for anything unparseable, so callers decide whether a bad
    stamp is a validation error (``models``) or simply a row that cannot match
    (``targeting``). Never raises.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_iso(moment: datetime) -> str:
    """Canonical storage form for an arbitrary datetime. Naive input is UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()
