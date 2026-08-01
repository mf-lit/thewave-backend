"""Deciding whether a change in availability is worth a push.

Pure functions over a notification and the seat count just read — no database,
no network — so the rules that actually reach users are directly testable.

Three rules, the first two carried over exactly:

* ``below_threshold`` fires the first time availability falls to or below each
  configured threshold. Every threshold crossed in one step is recorded, so a
  drop from 10 to 0 against thresholds [5, 2] notifies once and never re-fires
  for either.
* ``above_zero`` fires on the transition from "nothing left" to "something
  available", judged against the reading stored *before* this cycle's write.
* ``quiet_session`` fires once, when a session that is about to start still has
  at least its threshold free. *When* that check happens is decided entirely by
  ``next_check_at``, so this stays a function of availability alone.

``any_quiet_session`` gets its own entry point, `evaluate_any_quiet`, because it
is judged once per *matched session* rather than once per row; the worker owns
the iteration, this module still owns the rule.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .models import ABOVE_ZERO, BELOW_THRESHOLD, QUIET_SESSION, Notification


@dataclass(frozen=True)
class Decision:
    notify: bool
    message: str = ""
    threshold: Optional[int] = None
    newly_crossed: Tuple[int, ...] = ()


NO_ACTION = Decision(notify=False)


def evaluate(notification: Notification, availability: int) -> Decision:
    """Whether ``availability`` should trigger a push for this notification."""
    if notification.notification_type == BELOW_THRESHOLD:
        already_notified = set(notification.notified_thresholds)
        newly_crossed = tuple(
            threshold
            for threshold in (notification.thresholds or [])
            if availability <= threshold and threshold not in already_notified
        )
        if not newly_crossed:
            return NO_ACTION
        first = newly_crossed[0]
        return Decision(
            notify=True,
            message=f"Availability ({availability}) has fallen below threshold ({first})",
            threshold=first,
            newly_crossed=newly_crossed,
        )

    if notification.notification_type == ABOVE_ZERO:
        previous = notification.last_checked_availability
        if availability > 0 and (previous is None or previous == 0):
            return Decision(
                notify=True,
                message=f"Availability ({availability}) has increased above zero",
            )
        return NO_ACTION

    if notification.notification_type == QUIET_SESSION:
        # Nothing else writes a reading for this type, so one being present
        # means the single check has already happened. Firing once is therefore
        # a property of the row rather than of how it happens to be scheduled.
        if notification.last_checked_availability is not None:
            return NO_ACTION
        minimum = notification.minimum_slots
        if minimum is None or availability < minimum:
            return NO_ACTION
        return Decision(
            notify=True,
            message=f"Session is quiet: {availability} slots remaining (minimum {minimum})",
        )

    return NO_ACTION


def evaluate_any_quiet(
    notification: Notification, performance_ak: str, availability: int
) -> Decision:
    """Whether an any_quiet_session row should fire for one matched session.

    The window test is deliberately absent, for the same reason quiet_session's
    timing lives in ``next_check_at``: *which* sessions are candidates is a
    scheduling question, so this stays a function of seats and history alone.

    Note the asymmetry with quiet_session, which reads
    ``last_checked_availability`` as its fired-flag. One row watching many
    sessions cannot do that — hence ``notified_performances``, which records
    the firing per session rather than per row.
    """
    if performance_ak in notification.notified_performances:
        return NO_ACTION
    minimum = notification.minimum_slots
    if minimum is None or availability < minimum:
        return NO_ACTION
    return Decision(
        notify=True,
        message=f"Session is quiet: {availability} slots remaining (minimum {minimum})",
    )
