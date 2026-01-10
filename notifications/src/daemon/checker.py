"""Availability checker for notifications."""
import logging
from datetime import datetime
from typing import Dict, List, Tuple

from src.storage.sqlite import SQLiteStorage
from src.utils.calendar import (
    extract_availability_by_side,
    fetch_calendar_data_for_dates,
    find_performance_by_ak,
    get_notification_dates,
)

logger = logging.getLogger(__name__)


def is_session_in_past(notification: Dict) -> bool:
    """Check if a session is in the past."""
    date_str = notification.get("date")
    time_str = notification.get("time")

    if not date_str or not time_str:
        return False

    try:
        session_datetime = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        return session_datetime < datetime.now()
    except ValueError:
        logger.warning(f"Invalid date/time format in notification {notification.get('notification_id')}")
        return False


def check_notification_conditions(notification: Dict, current_availability: int) -> Tuple[bool, str]:
    """Check if notification conditions are met.

    Returns:
        Tuple of (should_notify, message)
    """
    notification_type = notification.get("notification_type")
    last_checked = notification.get("last_checked_availability")

    if notification_type == "below_threshold":
        thresholds = notification.get("thresholds", [])
        notified_thresholds = notification.get("notified_thresholds", [])

        for threshold in thresholds:
            if current_availability <= threshold and threshold not in notified_thresholds:
                return True, f"Availability ({current_availability}) has fallen below threshold ({threshold})"

    elif notification_type == "above_zero":
        if current_availability > 0 and (last_checked is None or last_checked == 0):
            return True, f"Availability ({current_availability}) has increased above zero"

    return False, ""


def process_threshold_notification(
    notification: Dict, current_availability: int, storage: SQLiteStorage
) -> int:
    """Process threshold notification and return the first crossed threshold.

    Returns:
        The first threshold that was crossed, or None
    """
    thresholds = notification.get("thresholds", [])
    notified_thresholds = notification.get("notified_thresholds", [])

    newly_crossed = [
        t for t in thresholds
        if current_availability <= t and t not in notified_thresholds
    ]

    if newly_crossed:
        notified_thresholds.extend(newly_crossed)
        storage.update_notification(
            notification.get("client_id"),
            notification.get("notification_id"),
            {"notified_thresholds": notified_thresholds},
        )
        return newly_crossed[0]

    return None


def process_single_notification(
    notification: Dict, calendar_data: Dict, storage: SQLiteStorage
) -> None:
    """Process a single notification and send alerts if conditions are met."""
    from src.daemon.notifier import send_notification

    notification_id = notification.get("notification_id")
    client_id = notification.get("client_id")

    # Delete past notifications
    if is_session_in_past(notification):
        logger.info(f"Deleting past notification {notification_id}")
        storage.delete_notification(client_id, notification_id)
        return

    # Validate performance exists
    performance_ak = notification.get("performance_ak")
    if not performance_ak:
        logger.warning(f"Notification {notification_id} has no performance_ak")
        return

    performance = find_performance_by_ak(calendar_data, performance_ak)
    if not performance:
        logger.warning(f"Performance {performance_ak} not found in calendar data")
        return

    # Get current availability
    side = notification.get("side")
    current_availability = extract_availability_by_side(performance, side)
    if current_availability is None:
        logger.warning(f"Could not extract availability for side {side} in performance {performance_ak}")
        return

    # Update last checked availability
    storage.update_notification(client_id, notification_id, {"last_checked_availability": current_availability})

    # Check and send notification if conditions met
    should_notify, message = check_notification_conditions(notification, current_availability)
    if not should_notify:
        return

    threshold_value = None
    if notification.get("notification_type") == "below_threshold":
        threshold_value = process_threshold_notification(notification, current_availability, storage)

    send_notification(notification, client_id, message, current_availability, threshold_value)
    logger.info(f"Sent notification for {notification_id}: {message}")


def check_availability(storage: SQLiteStorage) -> None:
    """Check availability for all notifications and trigger notifications as needed."""
    logger.info("Starting availability check")

    try:
        notifications = storage.get_all_notifications()
        logger.info(f"Found {len(notifications)} notifications to check")

        if not notifications:
            return

        unique_dates = get_notification_dates(notifications)
        if not unique_dates:
            logger.warning("No valid dates found in notifications")
            return

        logger.info(f"Fetching calendar data for {len(unique_dates)} specific date(s): {', '.join(unique_dates)}")

        try:
            calendar_data = fetch_calendar_data_for_dates(unique_dates)
        except Exception as e:
            logger.error(f"Failed to fetch calendar data: {e}")
            return

        for notification in notifications:
            try:
                process_single_notification(notification, calendar_data, storage)
            except Exception as e:
                logger.error(f"Error processing notification {notification.get('notification_id')}: {e}", exc_info=True)

        logger.info("Completed availability check")

    except Exception as e:
        logger.error(f"Error in availability check: {e}", exc_info=True)

