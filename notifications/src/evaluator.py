"""Deciding whether a change in availability is worth a push.

Pure functions over a notification and the seat count just read — no database,
no network — so the rules that actually reach users are directly testable.

Two rules, both carried over exactly:

* ``below_threshold`` fires the first time availability falls to or below each
  configured threshold. Every threshold crossed in one step is recorded, so a
  drop from 10 to 0 against thresholds [5, 2] notifies once and never re-fires
  for either.
* ``above_zero`` fires on the transition from "nothing left" to "something
  available", judged against the reading stored *before* this cycle's write.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from .models import ABOVE_ZERO, BELOW_THRESHOLD, Notification


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
