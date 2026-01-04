"""Notification sender (placeholder for future API integration)."""
import logging
from typing import Dict

from src.storage.dynamodb import DynamoDBStorage

logger = logging.getLogger(__name__)

storage = DynamoDBStorage()


def send_notification(notification: Dict, client_id: str, message: str):
    """Send a notification to a client via FCM.

    Retrieves the client's FCM token and sends a push notification.
    This is a placeholder implementation that logs the notification.
    In the future, this will call Firebase Cloud Messaging API.

    Args:
        notification: Notification dictionary
        client_id: UUID of the client to notify
        message: Notification message
    """
    # Retrieve FCM token for the client
    fcm_token = storage.get_client_token(client_id)

    if not fcm_token:
        logger.warning(
            f"No FCM token found for client {client_id}. Skipping notification."
        )
        return

    logger.info(
        f"NOTIFICATION for client {client_id}: "
        f"Session {notification.get('title')} ({notification.get('date')} {notification.get('time')}) - {message}"
    )
    logger.info(f"Notification details: {notification}")
    logger.info(f"FCM token: {fcm_token[:20]}... (truncated for security)")

    # TODO: Replace this with actual Firebase Cloud Messaging API call
    # Example using firebase-admin SDK:
    # from firebase_admin import messaging
    # message = messaging.Message(
    #     notification=messaging.Notification(
    #         title=f"Session Alert: {notification.get('title')}",
    #         body=message,
    #     ),
    #     token=fcm_token,
    # )
    # response = messaging.send(message)
    # logger.info(f"Successfully sent message: {response}")

