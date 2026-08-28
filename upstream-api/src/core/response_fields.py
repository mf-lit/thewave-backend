"""Trim the calendar response to the fields clients actually read.

The upstream ticketing platform is a generic events product, so every
performance carries a `fields` object built for football fixtures and seated
venues: team logos, competition names, seat-map ids, button configs. None of it
is read by the app, and on a seven-day response it is a third of the payload.

Applied on the way out of the API rather than on the way in from upstream: the
history files keep the full object, so a field that turns out to be wanted
later is already archived rather than needing a backfill.
"""

import logging
import os

logger = logging.getLogger(__name__)

# The keys `PerformanceFields` declares in the Flutter client
# (lib/models/performance.dart). Derived from the model rather than from what
# upstream currently sends - seatMapId, for one, is declared by the client and
# has never appeared in a response, and stripping it the day upstream starts
# sending it would be a confusing bug to chase.
CLIENT_FIELDS = frozenset({
    "description",
    "image",
    "imageSmall",
    "place",
    "price",
    "priceCurrency",
    "priceLabel",
    "seatAssignmentType",
    "seatMapId",
    "tagline",
    "title",
})


def _strip_enabled() -> bool:
    """Read at call time so the flag can be flipped by a restart alone."""
    value = os.getenv("STRIP_UNUSED_FIELDS", "true").strip().strip("\"'").lower()
    return value not in ("false", "0", "no")


def strip_unused_fields(response_data: dict) -> dict:
    """
    Drop the `fields` keys no client reads.

    Never mutates response_data in place, for the same reason
    apply_upgrade_gate does not: day dicts served from main.py's _day_cache are
    shared across every caller requesting that date, and save_daily_history
    writes from them. An in-place strip would empty the cache for everyone and
    bake the reduced payload into the archive permanently.

    Args:
        response_data: Calendar response data with a "days" list.

    Returns:
        dict: response_data, trimmed or not.
    """
    if not _strip_enabled():
        return response_data

    if not isinstance(response_data, dict):
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
            fields = performance.get("fields") if isinstance(performance, dict) else None
            if not isinstance(fields, dict):
                new_performances.append(performance)
                continue

            kept = {k: v for k, v in fields.items() if k in CLIENT_FIELDS}
            if len(kept) == len(fields):
                # Nothing to drop - keep the original rather than copying.
                new_performances.append(performance)
                continue

            new_performances.append({**performance, "fields": kept})

        new_days.append({**day, "performances": new_performances})

    return {**response_data, "days": new_days}
