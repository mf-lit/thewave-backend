"""Notification sender via Firebase Cloud Messaging."""
import json
import os
import logging
from typing import Dict, Optional

import firebase_admin
from firebase_admin import credentials, messaging

from src.storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)

storage = SQLiteStorage()

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


def send_notification(notification: Dict, client_id: str, message: str, availability: int, threshold: Optional[int] = None):
    """Send a notification to a client via FCM.

    Retrieves the client's FCM token and sends a push notification via Firebase.

    Args:
        notification: Notification dictionary
        client_id: UUID of the client to notify
        message: Notification message
        availability: Current availability count for the session
        threshold: The threshold that was crossed (for below_threshold type), None for above_zero
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

    # Get session title
    session_title = notification.get("title", "Session")

    # Prepare data payload for app processing
    # FCM data payload values must be strings
    # Data-only messages ensure consistent delivery to onMessageReceived() regardless of app state
    data_payload = {
        "performance_ak": str(notification.get("performance_ak", "")),
        "date": str(notification.get("date", "")),
        "time": str(notification.get("time", "")),
        "side": str(notification.get("side", "")),
        "session_title": str(session_title),
        "availability": str(availability),
        "notification_type": str(notification.get("notification_type", "")),
        "notification_id": str(notification.get("notification_id", "")),
        "threshold": str(threshold) if threshold is not None else "",
    }

    # Log the payload being sent
    logger.info(
        f"Sending FCM data-only message to client {client_id} with payload: {json.dumps(data_payload, indent=2)}"
    )

    # Create FCM message with data payload only (no notification payload)
    # This ensures all messages are delivered to onMessageReceived() for consistent handling
    fcm_message = messaging.Message(
        data=data_payload,
        token=fcm_token,
    )

    try:
        # Send the message
        response = messaging.send(fcm_message)
        logger.info(
            f"Successfully sent FCM data-only message to client {client_id}. "
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

