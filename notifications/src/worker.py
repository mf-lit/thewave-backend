"""The availability checker.

Each cycle: take the notifications whose next check is due, fetch calendar
data for just those dates, and for each one either delete it (session has
started), record the new seat count, or record it and push.

The loop sleeps until the next scheduled check rather than ticking on a fixed
timer, and waits on an Event so SIGTERM is acted on immediately instead of up
to a minute later.
"""
from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime, timedelta, timezone
from typing import Optional

from . import clock, evaluator, scheduling
from .calendar_client import CalendarData, CalendarError, availability_for_side
from .models import BELOW_THRESHOLD, QUIET_SESSION, Notification, duration_hours
from .services import Services
from .settings import configure_logging

logger = logging.getLogger(__name__)

MIN_SLEEP_SECONDS = 30
MAX_SLEEP_SECONDS = 60

# How long a quiet_session keeps retrying for usable calendar data before it is
# abandoned — ten attempts at FALLBACK_MINUTES. Its check is only meaningful
# near the moment it was scheduled for: "starting soon, and quiet" delivered
# hours late describes a seat count that no longer means what the user asked
# about, and the push carries no reading time for the app to notice.
QUIET_GRACE_MINUTES = 30


def _parse_created_at(value: Optional[str]) -> Optional[datetime]:
    """Read a stored ``created_at``, which is naive UTC ISO-8601."""
    try:
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class Worker:
    def __init__(self, services: Services):
        self.services = services

    # -- one pass ---------------------------------------------------------

    def run_once(self) -> None:
        """Check every notification that is currently due."""
        due = self.services.notifications.due()
        if not due:
            return

        dates = sorted({n.date for n in due if n.date})
        logger.info(
            "Checking %d notification(s) across %d date(s): %s",
            len(due),
            len(dates),
            ", ".join(dates),
        )

        try:
            calendar = self.services.calendar.fetch_dates(dates)
        except CalendarError as exc:
            logger.warning("Skipping cycle, calendar unavailable: %s", exc)
            return

        for notification in due:
            try:
                self.process(notification, calendar)
            except Exception:  # one bad notification must not stall the rest
                logger.exception(
                    "Failed to process notification %s", notification.notification_id
                )

    def process(self, notification: Notification, calendar: CalendarData) -> None:
        repository = self.services.notifications

        if clock.is_past(notification.date, notification.time):
            logger.info(
                "Removing notification %s; session %s %s has started",
                notification.notification_id,
                notification.date,
                notification.time,
            )
            repository.delete(notification.client_id, notification.notification_id)
            return

        performance = calendar.find_performance(notification.performance_ak)
        if performance is None:
            # Previously this left next_check_at untouched, so a performance
            # that vanished from the calendar was re-fetched every cycle
            # forever. Back off to the shortest normal interval instead.
            self._back_off(
                notification,
                f"Performance {notification.performance_ak} not in calendar data",
            )
            return

        availability = availability_for_side(performance, notification.side)
        if availability is None:
            self._back_off(
                notification,
                f"No '{notification.side}' side on performance "
                f"{notification.performance_ak}",
            )
            return

        if notification.notification_type == QUIET_SESSION:
            # Checked once, at time_before. Parking it past the session start
            # means it can never come due again before the branch above deletes
            # it, so no "already fired" column is needed.
            next_check_at = self._retire_at(notification)
        else:
            next_check_at = self._next_check(
                scheduling.interval_minutes(
                    availability, clock.session_start(notification.date, notification.time)
                )
            )

        decision = evaluator.evaluate(notification, availability)

        # Record the reading first: `decision` was taken against the previous
        # value, and this must not be lost if the push itself fails.
        repository.record_check(notification, availability, next_check_at)

        if not decision.notify:
            return

        if notification.notification_type == BELOW_THRESHOLD and decision.newly_crossed:
            repository.record_notified_thresholds(
                notification,
                list(notification.notified_thresholds) + list(decision.newly_crossed),
            )

        logger.info(
            "Notification %s triggered: %s",
            notification.notification_id,
            decision.message,
        )
        self.services.notifier.send(notification, availability, decision.threshold)

    @staticmethod
    def _next_check(minutes: int) -> str:
        return clock.utc_stamp(clock.now_utc() + timedelta(minutes=minutes))

    def _retire_at(self, notification: Notification) -> str:
        """Where a finished quiet_session waits to be deleted."""
        return clock.just_after_start(notification.date, notification.time) or self._next_check(
            scheduling.FALLBACK_MINUTES
        )

    def _back_off(self, notification: Notification, reason: str) -> None:
        """Reschedule after a cycle that produced no usable seat count.

        A quiet_session gets only one meaningful check, so rather than retrying
        until the session starts it is abandoned once its window has passed.
        """
        if notification.notification_type == QUIET_SESSION and self._quiet_window_closed(
            notification
        ):
            logger.warning(
                "%s; abandoning quiet_session %s, its check window has passed",
                reason,
                notification.notification_id,
            )
            self.services.notifications.reschedule(notification, self._retire_at(notification))
            return

        logger.warning("%s; retrying in %d minutes", reason, scheduling.FALLBACK_MINUTES)
        self.services.notifications.reschedule(
            notification, self._next_check(scheduling.FALLBACK_MINUTES)
        )

    @staticmethod
    def _quiet_window_closed(notification: Notification, now: Optional[datetime] = None) -> bool:
        """Whether a quiet_session has waited too long for usable data."""
        fire_at = clock.hours_before(
            notification.date, notification.time, duration_hours(notification.time_before)
        )
        if fire_at is None:
            return False

        start = datetime.strptime(fire_at, clock.STAMP_FORMAT).replace(tzinfo=timezone.utc)
        # A notification created inside its own window was never going to be
        # checked at the nominal time, so the grace runs from creation instead.
        created = _parse_created_at(notification.created_at)
        if created is not None and created > start:
            start = created
        return (now or clock.now_utc()) > start + timedelta(minutes=QUIET_GRACE_MINUTES)

    # -- loop -------------------------------------------------------------

    def seconds_until_next_cycle(self) -> float:
        """Sleep just long enough to reach the next scheduled check."""
        next_due = self.services.notifications.next_due_at()
        if next_due is None:
            return MIN_SLEEP_SECONDS
        try:
            moment = datetime.strptime(next_due, clock.STAMP_FORMAT).replace(
                tzinfo=timezone.utc
            )
        except (TypeError, ValueError):
            return MIN_SLEEP_SECONDS
        remaining = (moment - clock.now_utc()).total_seconds()
        return max(MIN_SLEEP_SECONDS, min(MAX_SLEEP_SECONDS, remaining))

    def heartbeat(self) -> None:
        """Touch a file the container healthcheck watches."""
        path = self.services.settings.heartbeat_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(clock.utc_now_iso())
        except OSError as exc:
            logger.warning("Could not write heartbeat to %s: %s", path, exc)

    def run_forever(self, stop: Optional[threading.Event] = None) -> None:
        stop = stop or threading.Event()
        logger.info("Worker started")
        while not stop.is_set():
            try:
                self.run_once()
                self.heartbeat()
            except Exception:
                logger.exception("Check cycle failed")
            stop.wait(self.seconds_until_next_cycle())
        logger.info("Worker stopped")


def main() -> None:
    configure_logging()
    services = Services.build()
    worker = Worker(services)

    stop = threading.Event()

    def shutdown(signum, _frame):
        logger.info("Received signal %s, finishing current cycle", signum)
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    worker.run_forever(stop)


if __name__ == "__main__":
    main()
