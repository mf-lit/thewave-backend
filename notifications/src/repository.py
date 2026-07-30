"""Data access.

Every statement is parameterised and written out in full — the previous
implementation assembled ``UPDATE`` statements by interpolating dictionary
keys into an f-string, which meant the set of writable columns was whatever
the caller happened to pass.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Iterable, List, Optional, Sequence

from . import clock
from .db import Database
from .models import BELOW_THRESHOLD, Notification, NotificationRequest

logger = logging.getLogger(__name__)

_SELECT = """
SELECT client_id, notification_id, performance_ak, date, time, side, title,
       notification_type, thresholds, notified_thresholds,
       last_checked_availability, next_check_at, created_at
FROM notifications
"""


class NotificationRepository:
    def __init__(self, database: Database):
        self.db = database

    def create(self, client_id: str, request: NotificationRequest, title: str) -> Notification:
        notification_id = str(uuid.uuid4())
        created_at = clock.utc_now_iso()
        is_threshold = request.notification_type == BELOW_THRESHOLD

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO notifications (
                    client_id, notification_id, performance_ak, date, time, side,
                    title, notification_type, thresholds, notified_thresholds,
                    last_checked_availability, next_check_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    client_id,
                    notification_id,
                    request.performance_ak,
                    request.date,
                    request.time,
                    request.side,
                    title,
                    request.notification_type,
                    json.dumps(request.thresholds) if is_threshold else None,
                    json.dumps([]) if is_threshold else None,
                    created_at,
                ),
            )

        return Notification(
            client_id=client_id,
            notification_id=notification_id,
            performance_ak=request.performance_ak,
            date=request.date,
            time=request.time,
            side=request.side,
            title=title,
            notification_type=request.notification_type,
            created_at=created_at,
            thresholds=list(request.thresholds) if is_threshold else None,
            notified_thresholds=[] if is_threshold else [],
        )

    def list_for_client(self, client_id: str) -> List[Notification]:
        rows = self.db.connection().execute(
            _SELECT + "WHERE client_id = ?", (client_id,)
        )
        return [Notification.from_row(row) for row in rows]

    def get(self, client_id: str, notification_id: str) -> Optional[Notification]:
        row = self.db.connection().execute(
            _SELECT + "WHERE client_id = ? AND notification_id = ?",
            (client_id, notification_id),
        ).fetchone()
        return Notification.from_row(row) if row else None

    def delete(self, client_id: str, notification_id: str) -> bool:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM notifications WHERE client_id = ? AND notification_id = ?",
                (client_id, notification_id),
            )
        return cursor.rowcount > 0

    def delete_for_client(self, client_id: str) -> int:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM notifications WHERE client_id = ?", (client_id,)
            )
        return cursor.rowcount

    def due(self) -> List[Notification]:
        """Notifications whose next check is unset or already past."""
        rows = self.db.connection().execute(
            _SELECT + "WHERE next_check_at IS NULL OR next_check_at <= datetime('now')"
        )
        return [Notification.from_row(row) for row in rows]

    def next_due_at(self) -> Optional[str]:
        """The earliest scheduled check, or None if any check is already due."""
        row = self.db.connection().execute(
            "SELECT min(next_check_at) AS next, "
            "       sum(next_check_at IS NULL) AS unscheduled "
            "FROM notifications"
        ).fetchone()
        if row is None or row["unscheduled"]:
            return None
        return row["next"]

    def all(self) -> List[Notification]:
        rows = self.db.connection().execute(_SELECT + "ORDER BY date, time")
        return [Notification.from_row(row) for row in rows]

    def record_check(
        self, notification: Notification, availability: int, next_check_at: str
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE notifications SET last_checked_availability = ?, next_check_at = ? "
                "WHERE client_id = ? AND notification_id = ?",
                (
                    availability,
                    next_check_at,
                    notification.client_id,
                    notification.notification_id,
                ),
            )

    def reschedule(self, notification: Notification, next_check_at: str) -> None:
        """Move the next check without recording an availability reading."""
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE notifications SET next_check_at = ? "
                "WHERE client_id = ? AND notification_id = ?",
                (next_check_at, notification.client_id, notification.notification_id),
            )

    def record_notified_thresholds(
        self, notification: Notification, thresholds: Sequence[int]
    ) -> None:
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE notifications SET notified_thresholds = ? "
                "WHERE client_id = ? AND notification_id = ?",
                (
                    json.dumps(list(thresholds)),
                    notification.client_id,
                    notification.notification_id,
                ),
            )

    def clear_notified_thresholds(self) -> int:
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE notifications SET notified_thresholds = '[]' "
                "WHERE notification_type = ?",
                (BELOW_THRESHOLD,),
            )
        return cursor.rowcount

    def client_ids(self) -> List[str]:
        rows = self.db.connection().execute(
            "SELECT DISTINCT client_id FROM notifications"
        )
        return [row["client_id"] for row in rows]


class ClientRepository:
    def __init__(self, database: Database):
        self.db = database

    def upsert_token(self, client_id: str, fcm_token: str) -> str:
        """Store a client's FCM token. Returns the ``updated_at`` written."""
        updated_at = clock.utc_now_iso()
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO clients (client_id, fcm_token, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET
                    fcm_token = excluded.fcm_token,
                    updated_at = excluded.updated_at
                """,
                (client_id, fcm_token, updated_at),
            )
        return updated_at

    def get_token(self, client_id: str) -> Optional[str]:
        row = self.db.connection().execute(
            "SELECT fcm_token FROM clients WHERE client_id = ?", (client_id,)
        ).fetchone()
        return row["fcm_token"] if row else None

    def delete_token(self, client_id: str) -> bool:
        with self.db.transaction() as conn:
            cursor = conn.execute("DELETE FROM clients WHERE client_id = ?", (client_id,))
        return cursor.rowcount > 0

    def existing_ids(self, client_ids: Iterable[str]) -> set:
        """Which of ``client_ids`` currently hold a token."""
        wanted = list(client_ids)
        if not wanted:
            return set()
        placeholders = ",".join("?" * len(wanted))
        rows = self.db.connection().execute(
            f"SELECT client_id FROM clients WHERE client_id IN ({placeholders})", wanted
        )
        return {row["client_id"] for row in rows}

    def ids_with_blank_tokens(self) -> List[str]:
        rows = self.db.connection().execute(
            "SELECT client_id FROM clients WHERE fcm_token IS NULL OR trim(fcm_token) = ''"
        )
        return [row["client_id"] for row in rows]

    def count(self) -> int:
        return self.db.connection().execute("SELECT count(*) AS n FROM clients").fetchone()["n"]
