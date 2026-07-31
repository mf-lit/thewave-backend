"""Push delivery via Firebase Cloud Messaging.

The ``data`` map and the title/body strings below are a contract with the
Flutter app: it parses the data keys directly, and iOS renders the alert from
the notification block when the app is backgrounded. The formatting mirrors
the app's own NotificationFormatter so an auto-displayed banner looks the same
as an in-app one. Do not reword these without shipping an app change.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Protocol, Tuple

from .models import ABOVE_ZERO, QUIET_SESSION, Notification
from .repository import ClientRepository

logger = logging.getLogger(__name__)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class PushError(RuntimeError):
    """Delivery failed."""


class TokenRejected(PushError):
    """The token is dead — unregistered, or from another Firebase project."""


@dataclass(frozen=True)
class PushMessage:
    token: str
    title: str
    body: str
    data: Dict[str, str]


class Sender(Protocol):
    def send(self, message: PushMessage) -> str:
        """Deliver ``message``, returning a provider message id."""


def fingerprint(token: str) -> str:
    """A loggable stand-in for an FCM token."""
    if len(token) <= 14:
        return "…"
    return f"{token[:6]}…{token[-4:]}"


def _ordinal(day: int) -> str:
    if 11 <= day <= 13:
        return f"{day}th"
    return {1: f"{day}st", 2: f"{day}nd", 3: f"{day}rd"}.get(day % 10, f"{day}th")


def format_date_short(date_str: str) -> str:
    """``2026-01-05`` -> ``5th Jan``; unparseable input passes through."""
    try:
        parsed = datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return date_str
    return f"{_ordinal(parsed.day)} {_MONTHS[parsed.month - 1]}"


def display_strings(notification: Notification, availability: int) -> Tuple[str, str]:
    """The (title, body) iOS shows when the app is backgrounded or killed."""
    title = (
        f"{notification.title or 'Session'}: "
        f"{format_date_short(notification.date)} at {notification.time}"
    )
    if notification.notification_type == ABOVE_ZERO:
        body = "A session has become available"
    elif notification.notification_type == QUIET_SESSION:
        body = f"Quiet session: {availability} slots remaining on the {notification.side}"
    else:
        body = f"Availability dropped to {availability} on the {notification.side}"
    return title, body


def data_payload(
    notification: Notification, availability: int, threshold: Optional[int]
) -> Dict[str, str]:
    """The FCM data map the app parses. All values must be strings."""
    return {
        "performance_ak": str(notification.performance_ak or ""),
        "date": str(notification.date or ""),
        "time": str(notification.time or ""),
        "side": str(notification.side or ""),
        "session_title": str(notification.title or "Session"),
        "availability": str(availability),
        "notification_type": str(notification.notification_type or ""),
        "notification_id": str(notification.notification_id or ""),
        # `threshold` is the seat count at or *below* which below_threshold
        # fires; `minimum_slots` is the count at or *above* which quiet_session
        # does. Opposite senses, so they stay separate keys rather than one
        # whose meaning the app would have to infer from notification_type.
        "threshold": str(threshold) if threshold is not None else "",
        "minimum_slots": (
            "" if notification.minimum_slots is None else str(notification.minimum_slots)
        ),
    }


class FirebaseSender:
    """Sends through firebase-admin, initialised on first use."""

    def __init__(self, credentials_path: Path):
        self.credentials_path = Path(credentials_path)
        self._app = None

    def _ensure_app(self):
        if self._app is not None:
            return self._app

        import firebase_admin
        from firebase_admin import credentials

        try:
            self._app = firebase_admin.get_app()
            return self._app
        except ValueError:
            pass

        if not self.credentials_path.exists():
            raise PushError(
                f"Firebase credentials not found at {self.credentials_path}. "
                "Set GOOGLE_APPLICATION_CREDENTIALS or mount the config directory."
            )

        self._app = firebase_admin.initialize_app(
            credentials.Certificate(str(self.credentials_path))
        )
        logger.info("Firebase initialised from %s", self.credentials_path)
        return self._app

    def send(self, message: PushMessage) -> str:
        from firebase_admin import exceptions, messaging

        self._ensure_app()
        payload = messaging.Message(
            notification=messaging.Notification(title=message.title, body=message.body),
            data=message.data,
            token=message.token,
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(
                headers={"apns-priority": "10", "apns-push-type": "alert"},
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(
                        alert=messaging.ApsAlert(title=message.title, body=message.body),
                        sound="default",
                        mutable_content=True,
                    ),
                ),
            ),
        )

        try:
            return messaging.send(payload)
        except (messaging.UnregisteredError, messaging.SenderIdMismatchError) as exc:
            raise TokenRejected(str(exc)) from exc
        except exceptions.FirebaseError as exc:
            raise PushError(str(exc)) from exc
        except Exception as exc:  # transport, auth, anything below firebase-admin
            raise PushError(f"{type(exc).__name__}: {exc}") from exc


class Notifier:
    """Looks up a client's token, sends, and reaps tokens FCM rejects."""

    def __init__(self, clients: ClientRepository, sender: Sender):
        self.clients = clients
        self.sender = sender

    def send(
        self, notification: Notification, availability: int, threshold: Optional[int] = None
    ) -> bool:
        token = self.clients.get_token(notification.client_id)
        if not token:
            logger.info(
                "No FCM token for client %s; skipping push for %s",
                notification.client_id,
                notification.notification_id,
            )
            return False

        title, body = display_strings(notification, availability)
        message = PushMessage(
            token=token,
            title=title,
            body=body,
            data=data_payload(notification, availability, threshold),
        )

        try:
            message_id = self.sender.send(message)
        except TokenRejected as exc:
            logger.warning(
                "FCM rejected token %s for client %s (%s); removing it",
                fingerprint(token),
                notification.client_id,
                exc,
            )
            self.clients.delete_token(notification.client_id)
            return False
        except PushError as exc:
            logger.warning(
                "Push to client %s failed: %s", notification.client_id, exc
            )
            return False

        logger.info(
            "Pushed to client %s via token %s: %r / %r (message %s)",
            notification.client_id,
            fingerprint(token),
            title,
            body,
            message_id,
        )
        return True
