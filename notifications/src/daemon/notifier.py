"""Notification sender via Firebase Cloud Messaging."""
import os
import logging
from typing import Dict, Optional

import firebase_admin
from firebase_admin import credentials, messaging

from src.storage.dynamodb import DynamoDBStorage

logger = logging.getLogger(__name__)

storage = DynamoDBStorage()

# Firebase app instance (singleton)
_firebase_app: Optional[firebase_admin.App] = None


def _initialize_firebase():
    """Initialize Firebase Admin SDK if not already initialized.

    Uses GOOGLE_APPLICATION_CREDENTIALS environment variable or defaults to
    google_application_credentials.json in the project root.
    """
    global _firebase_app

    if _firebase_app is not None:
        return _firebase_app

    try:
        # Check if Firebase is already initialized
        _firebase_app = firebase_admin.get_app()
        logger.info("Firebase Admin SDK already initialized")
        return _firebase_app
    except ValueError:
        # Not initialized yet, proceed to initialize
        pass

    # Get credentials file path
    creds_path = os.getenv(
        "GOOGLE_APPLICATION_CREDENTIALS", "google_application_credentials.json"
    )

    # Resolve to absolute path if relative
    if not os.path.isabs(creds_path):
        # Get project root (parent of src directory)
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        creds_path = os.path.join(project_root, creds_path)

    if not os.path.exists(creds_path):
        logger.error(
            f"Firebase credentials file not found at {creds_path}. "
            "Set GOOGLE_APPLICATION_CREDENTIALS environment variable or place "
            "google_application_credentials.json in project root."
        )
        return None

    try:
        cred = credentials.Certificate(creds_path)
        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info(f"Firebase Admin SDK initialized with credentials from {creds_path}")
        return _firebase_app
    except Exception as e:
        logger.error(f"Failed to initialize Firebase Admin SDK: {e}")
        return None


def send_notification(notification: Dict, client_id: str, message: str):
    """Send a notification to a client via FCM.

    Retrieves the client's FCM token and sends a push notification via Firebase.

    Args:
        notification: Notification dictionary
        client_id: UUID of the client to notify
        message: Notification message
    """
    # Initialize Firebase if not already done
    firebase_app = _initialize_firebase()
    if firebase_app is None:
        logger.error(
            "Firebase not initialized. Cannot send notification. "
            "Check Firebase credentials configuration."
        )
        return

    # Retrieve FCM token for the client
    fcm_token = storage.get_client_token(client_id)

    if not fcm_token:
        logger.warning(
            f"No FCM token found for client {client_id}. Skipping notification."
        )
        return

    # Build notification title
    session_title = notification.get("title", "Session")
    notification_title = f"Session Alert: {session_title}"

    # Prepare data payload for app processing
    # FCM data payload values must be strings
    data_payload = {
        "client_id": str(client_id),
        "notification_id": str(notification.get("notification_id", "")),
        "performance_ak": str(notification.get("performance_ak", "")),
        "date": str(notification.get("date", "")),
        "time": str(notification.get("time", "")),
        "side": str(notification.get("side", "")),
        "notification_type": str(notification.get("notification_type", "")),
    }

    # Create FCM message with both notification and data payloads
    fcm_message = messaging.Message(
        notification=messaging.Notification(
            title=notification_title,
            body=message,
        ),
        data=data_payload,
        token=fcm_token,
    )

    try:
        # Send the message
        response = messaging.send(fcm_message)
        logger.info(
            f"Successfully sent FCM notification to client {client_id}. "
            f"Message ID: {response}. Session: {session_title} ({notification.get('date')} {notification.get('time')})"
        )
    except messaging.UnregisteredError:
        # Token is invalid or unregistered - delete it
        logger.warning(
            f"FCM token for client {client_id} is invalid or unregistered. Deleting token."
        )
        storage.delete_client_token(client_id)
    except messaging.InvalidArgumentError as e:
        logger.error(
            f"Invalid argument when sending FCM notification to client {client_id}: {e}"
        )
    except Exception as e:
        logger.error(
            f"Failed to send FCM notification to client {client_id}: {e}",
            exc_info=True,
        )

