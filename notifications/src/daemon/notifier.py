"""Notification sender via Firebase Cloud Messaging."""
import json
import logging
import os
from datetime import datetime
from typing import Dict, Optional

import firebase_admin
from firebase_admin import credentials, exceptions, messaging

from src.storage.sqlite import SQLiteStorage

logger = logging.getLogger(__name__)

storage = SQLiteStorage()
_firebase_app: Optional[firebase_admin.App] = None


_MONTH_ABBREV = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


def _day_with_ordinal(day: int) -> str:
    if 11 <= day <= 13:
        return f"{day}th"
    return {1: f"{day}st", 2: f"{day}nd", 3: f"{day}rd"}.get(day % 10, f"{day}th")


def _format_date_short(date_str: str) -> str:
    """Format YYYY-MM-DD as e.g. '5th Jan'. Falls back to the raw string on parse error."""
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return f"{_day_with_ordinal(d.day)} {_MONTH_ABBREV[d.month - 1]}"
    except (ValueError, IndexError):
        return date_str


def _build_display_strings(
    notification: Dict, availability: int
) -> tuple[str, str]:
    """Build the (title, body) shown by iOS when the app is in background/killed.

    Matches the formatting in the Flutter app's NotificationFormatter so that
    iOS auto-displayed banners look the same as the in-app local notifications
    shown when the app is in foreground.
    """
    session_title = notification.get("title", "Session")
    date = notification.get("date", "")
    time = notification.get("time", "")
    side = notification.get("side", "")
    notification_type = notification.get("notification_type", "below_threshold")

    title = f"{session_title}: {_format_date_short(date)} at {time}"

    if notification_type == "above_zero":
        body = "A session has become available"
    else:
        body = f"Availability dropped to {availability} on the {side}"

    return title, body


def _get_credentials_path() -> str:
    """Get the Firebase credentials file path."""
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "config/google_application_credentials.json")

    if os.path.isabs(creds_path):
        return creds_path

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(project_root, creds_path)


def _initialize_firebase() -> Optional[firebase_admin.App]:
    """Initialize Firebase Admin SDK if not already initialized."""
    global _firebase_app

    if _firebase_app is not None:
        return _firebase_app

    try:
        _firebase_app = firebase_admin.get_app()
        logger.info("Firebase Admin SDK already initialized")
        return _firebase_app
    except ValueError:
        pass

    creds_path = _get_credentials_path()

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


def _build_data_payload(notification: Dict, availability: int, threshold: Optional[int]) -> Dict[str, str]:
    """Build the FCM data payload from notification data."""
    return {
        "performance_ak": str(notification.get("performance_ak", "")),
        "date": str(notification.get("date", "")),
        "time": str(notification.get("time", "")),
        "side": str(notification.get("side", "")),
        "session_title": str(notification.get("title", "Session")),
        "availability": str(availability),
        "notification_type": str(notification.get("notification_type", "")),
        "notification_id": str(notification.get("notification_id", "")),
        "threshold": str(threshold) if threshold is not None else "",
    }


def send_notification(
    notification: Dict,
    client_id: str,
    message: str,
    availability: int,
    threshold: Optional[int] = None,
) -> None:
    """Send a push notification to a client via FCM."""
    firebase_app = _initialize_firebase()
    if firebase_app is None:
        logger.error("Firebase not initialized. Cannot send notification.")
        return

    fcm_token = storage.get_client_token(client_id)
    if not fcm_token:
        logger.warning(f"No FCM token found for client {client_id}. Skipping notification.")
        return

    data_payload = _build_data_payload(notification, availability, threshold)
    title, body = _build_display_strings(notification, availability)
    logger.info(
        f"Sending FCM message to client {client_id}. "
        f"Title: {title!r}, Body: {body!r}, Data: {json.dumps(data_payload)}"
    )

    fcm_message = messaging.Message(
        notification=messaging.Notification(title=title, body=body),
        data=data_payload,
        token=fcm_token,
        android=messaging.AndroidConfig(priority='high'),
        apns=messaging.APNSConfig(
            headers={'apns-priority': '10', 'apns-push-type': 'alert'},
            payload=messaging.APNSPayload(
                aps=messaging.Aps(
                    alert=messaging.ApsAlert(title=title, body=body),
                    sound='default',
                    mutable_content=True,
                ),
            ),
        ),
    )

    try:
        response = messaging.send(fcm_message)
        session_title = notification.get("title", "Session")
        logger.info(
            f"Successfully sent FCM message to client {client_id}. "
            f"Message ID: {response}. Session: {session_title} ({notification.get('date')} {notification.get('time')})"
        )
    except messaging.UnregisteredError:
        logger.warning(f"FCM token for client {client_id} is unregistered. Deleting token.")
        storage.delete_client_token(client_id)
    except messaging.SenderIdMismatchError:
        logger.warning(f"FCM token for client {client_id} belongs to a different Firebase project. Deleting token.")
        storage.delete_client_token(client_id)
    except exceptions.InvalidArgumentError as e:
        logger.error(f"Invalid argument when sending FCM notification to client {client_id}: {e}")
    except Exception as e:
        logger.error(f"Failed to send FCM notification to client {client_id}: {e}", exc_info=True)

