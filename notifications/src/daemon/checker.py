"""Availability checker for notifications."""
import logging
from datetime import datetime, timedelta
from typing import Dict, List

from src.storage.sqlite import SQLiteStorage
from src.utils.calendar import (
    extract_availability_by_side,
    fetch_calendar_data_for_dates,
    find_performance_by_ak,
    get_notification_dates,
)

logger = logging.getLogger(__name__)


def is_session_in_past(notification: Dict) -> bool:
    """Check if a session is in the past.

    Args:
        notification: Notification dictionary with date and time

    Returns:
        True if the session is in the past, False otherwise
    """
    date_str = notification.get("date")
    time_str = notification.get("time")

    if not date_str or not time_str:
        return False

    try:
        # Parse date and time
        session_datetime = datetime.strptime(
            f"{date_str} {time_str}", "%Y-%m-%d %H:%M"
        )
        return session_datetime < datetime.now()
    except ValueError:
        logger.warning(
            f"Invalid date/time format in notification {notification.get('notification_id')}"
        )
        return False


def check_notification_conditions(
    notification: Dict, current_availability: int
) -> tuple[bool, str]:
    """Check if notification conditions are met.

    Args:
        notification: Notification dictionary
        current_availability: Current availability count

    Returns:
        Tuple of (should_notify, message)
    """
    notification_type = notification.get("notification_type")
    last_checked = notification.get("last_checked_availability")

    if notification_type == "below_threshold":
        thresholds = notification.get("thresholds", [])
        notified_thresholds = notification.get("notified_thresholds", [])

        # Check if any threshold is crossed that hasn't been notified yet
        for threshold in thresholds:
            if current_availability <= threshold and threshold not in notified_thresholds:
                return (
                    True,
                    f"Availability ({current_availability}) has fallen below threshold ({threshold})",
                )

    elif notification_type == "above_zero":
        # Check if availability increased from 0
        if current_availability > 0 and (last_checked is None or last_checked == 0):
            return (
                True,
                f"Availability ({current_availability}) has increased above zero",
            )

    return False, ""


def check_availability(storage: SQLiteStorage) -> None:
    """Check availability for all notifications and trigger notifications as needed.

    Args:
        storage: SQLite storage instance
    """
    logger.info("Starting availability check")

    try:
        # Get all notifications
        notifications = storage.get_all_notifications()
        logger.info(f"Found {len(notifications)} notifications to check")

        if not notifications:
            return

        # Get unique dates from notifications - only fetch these specific dates
        unique_dates = get_notification_dates(notifications)
        
        if not unique_dates:
            logger.warning("No valid dates found in notifications")
            return
        
        logger.info(
            f"Fetching calendar data for {len(unique_dates)} specific date(s): {', '.join(unique_dates)}"
        )

        # Fetch calendar data only for the specific dates that have notifications
        try:
            calendar_data = fetch_calendar_data_for_dates(unique_dates)
        except Exception as e:
            logger.error(f"Failed to fetch calendar data: {e}")
            return

        # Process each notification
        from src.daemon.notifier import send_notification

        for notification in notifications:
            try:
                # Check if session is in the past
                if is_session_in_past(notification):
                    logger.info(
                        f"Deleting past notification {notification.get('notification_id')}"
                    )
                    storage.delete_notification(
                        notification.get("client_id"),
                        notification.get("notification_id"),
                    )
                    continue

                # Find the performance
                performance_ak = notification.get("performance_ak")
                if not performance_ak:
                    logger.warning(
                        f"Notification {notification.get('notification_id')} has no performance_ak"
                    )
                    continue

                performance = find_performance_by_ak(calendar_data, performance_ak)
                if not performance:
                    logger.warning(
                        f"Performance {performance_ak} not found in calendar data"
                    )
                    continue

                # Extract availability for the specified side
                side = notification.get("side")
                current_availability = extract_availability_by_side(performance, side)

                if current_availability is None:
                    logger.warning(
                        f"Could not extract availability for side {side} in performance {performance_ak}"
                    )
                    continue

                # Update last checked availability
                storage.update_notification(
                    notification.get("client_id"),
                    notification.get("notification_id"),
                    {"last_checked_availability": current_availability},
                )

                # Check notification conditions
                should_notify, message = check_notification_conditions(
                    notification, current_availability
                )

                if should_notify:
                    # Determine the threshold that was crossed
                    threshold_value = None
                    if notification.get("notification_type") == "below_threshold":
                        thresholds = notification.get("thresholds", [])
                        notified_thresholds = notification.get(
                            "notified_thresholds", []
                        )
                        # Find all thresholds that were crossed
                        newly_crossed = []
                        for threshold in thresholds:
                            if (
                                current_availability <= threshold
                                and threshold not in notified_thresholds
                            ):
                                newly_crossed.append(threshold)
                                notified_thresholds.append(threshold)

                        # Update all newly crossed thresholds in one operation
                        if newly_crossed:
                            storage.update_notification(
                                notification.get("client_id"),
                                notification.get("notification_id"),
                                {"notified_thresholds": notified_thresholds},
                            )
                            # Use the first threshold that was crossed (matches the message)
                            threshold_value = newly_crossed[0] if newly_crossed else None

                    # Send notification
                    send_notification(
                        notification,
                        notification.get("client_id"),
                        message,
                        current_availability,
                        threshold_value,
                    )
                    logger.info(
                        f"Sent notification for {notification.get('notification_id')}: {message}"
                    )

            except Exception as e:
                logger.error(
                    f"Error processing notification {notification.get('notification_id')}: {e}",
                    exc_info=True,
                )
                continue

        logger.info("Completed availability check")

    except Exception as e:
        logger.error(f"Error in availability check: {e}", exc_info=True)

