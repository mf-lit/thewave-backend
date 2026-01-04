"""Notification sender (placeholder for future API integration)."""
import logging
from typing import Dict

logger = logging.getLogger(__name__)


def send_notification(notification: Dict, client_id: str, message: str):
    """Send a notification to a client.

    This is a placeholder implementation that logs the notification.
    In the future, this will call an actual notification API.

    Args:
        notification: Notification dictionary
        client_id: UUID of the client to notify
        message: Notification message
    """
    logger.info(
        f"NOTIFICATION for client {client_id}: "
        f"Session {notification.get('title')} ({notification.get('date')} {notification.get('time')}) - {message}"
    )
    logger.info(f"Notification details: {notification}")

    # TODO: Replace this with actual API call to notification service
    # Example:
    # notification_api_url = os.getenv("NOTIFICATION_API_URL")
    # requests.post(
    #     f"{notification_api_url}/notifications/{client_id}",
    #     json={"message": message, "notification": notification}
    # )

