"""SQLite storage layer for notifications."""
import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

JSON_FIELDS = ("thresholds", "notified_thresholds")


class SQLiteStorage:
    """Handles all SQLite operations for notifications."""

    def __init__(self, db_path: Optional[str] = None):
        """Initialize SQLite connection."""
        self.db_path = db_path or os.getenv("SQLITE_DB_PATH", "data/notifications.db")
        # Ensure the data directory exists
        db_path_obj = Path(self.db_path)
        db_path_obj.parent.mkdir(parents=True, exist_ok=True)
        self.conn = None
        self._connect()

    def _connect(self) -> None:
        """Establish database connection."""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def _dict_from_row(self, row: sqlite3.Row) -> Optional[Dict[str, Any]]:
        """Convert a SQLite Row to a dictionary, parsing JSON fields."""
        if row is None:
            return None
        result = dict(row)
        for field in JSON_FIELDS:
            if field in result and result[field]:
                result[field] = json.loads(result[field])
        return result

    def ensure_table_exists(self) -> None:
        """Create the notifications table if it doesn't exist."""
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                client_id TEXT NOT NULL,
                notification_id TEXT NOT NULL,
                performance_ak TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                side TEXT NOT NULL,
                title TEXT NOT NULL,
                notification_type TEXT NOT NULL,
                thresholds TEXT,
                notified_thresholds TEXT,
                last_checked_availability INTEGER,
                created_at TEXT NOT NULL,
                PRIMARY KEY (client_id, notification_id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_performance_ak ON notifications(performance_ak)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_date ON notifications(date)")
        self.conn.commit()

    def ensure_clients_table_exists(self) -> None:
        """Create the clients table if it doesn't exist."""
        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                client_id TEXT PRIMARY KEY,
                fcm_token TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        self.conn.commit()

    def create_notification(self, client_id: str, notification_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new notification and return the created item."""
        self.ensure_table_exists()

        notification_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        is_threshold_type = notification_data["notification_type"] == "below_threshold"

        thresholds_json = json.dumps(notification_data["thresholds"]) if is_threshold_type else None
        notified_thresholds_json = json.dumps([]) if is_threshold_type else None

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO notifications (
                client_id, notification_id, performance_ak, date, time, side, title,
                notification_type, thresholds, notified_thresholds,
                last_checked_availability, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                client_id,
                notification_id,
                notification_data["performance_ak"],
                notification_data["date"],
                notification_data["time"],
                notification_data["side"],
                notification_data["title"],
                notification_data["notification_type"],
                thresholds_json,
                notified_thresholds_json,
                notification_data.get("last_checked_availability"),
                now,
            ),
        )
        self.conn.commit()

        item = {
            "client_id": client_id,
            "notification_id": notification_id,
            "performance_ak": notification_data["performance_ak"],
            "date": notification_data["date"],
            "time": notification_data["time"],
            "side": notification_data["side"],
            "title": notification_data["title"],
            "notification_type": notification_data["notification_type"],
            "last_checked_availability": notification_data.get("last_checked_availability"),
            "created_at": now,
        }

        if is_threshold_type:
            item["thresholds"] = notification_data["thresholds"]
            item["notified_thresholds"] = []

        return item

    def get_notifications_by_client(self, client_id: str) -> List[Dict[str, Any]]:
        """Get all notifications for a specific client."""
        self.ensure_table_exists()
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM notifications WHERE client_id = ?", (client_id,))
        return [self._dict_from_row(row) for row in cursor.fetchall()]

    def get_notification_by_id(self, client_id: str, notification_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific notification by ID."""
        self.ensure_table_exists()
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM notifications WHERE client_id = ? AND notification_id = ?",
            (client_id, notification_id),
        )
        row = cursor.fetchone()
        return self._dict_from_row(row) if row else None

    def delete_notification(self, client_id: str, notification_id: str) -> bool:
        """Delete a notification. Returns True if deleted, False if not found."""
        self.ensure_table_exists()
        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM notifications WHERE client_id = ? AND notification_id = ?",
            (client_id, notification_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def update_notification(self, client_id: str, notification_id: str, updates: Dict[str, Any]) -> bool:
        """Update a notification. Returns True if updated, False if not found."""
        self.ensure_table_exists()

        if not updates:
            return False

        set_clauses = []
        values = []

        for key, value in updates.items():
            if key in JSON_FIELDS and isinstance(value, list):
                value = json.dumps(value)
            set_clauses.append(f"{key} = ?")
            values.append(value)

        values.extend([client_id, notification_id])

        cursor = self.conn.cursor()
        query = f"UPDATE notifications SET {', '.join(set_clauses)} WHERE client_id = ? AND notification_id = ?"
        cursor.execute(query, values)
        self.conn.commit()
        return cursor.rowcount > 0

    def get_all_notifications(self) -> List[Dict[str, Any]]:
        """Get all notifications across all clients (for daemon use)."""
        self.ensure_table_exists()
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM notifications")
        return [self._dict_from_row(row) for row in cursor.fetchall()]

    def create_or_update_client_token(self, client_id: str, fcm_token: str) -> Dict[str, Any]:
        """Create or update FCM token for a client."""
        self.ensure_clients_table_exists()
        now = datetime.utcnow().isoformat()

        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO clients (client_id, fcm_token, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                fcm_token = excluded.fcm_token,
                updated_at = excluded.updated_at
            """,
            (client_id, fcm_token, now),
        )
        self.conn.commit()
        return {"client_id": client_id, "fcm_token": fcm_token, "updated_at": now}

    def get_client_token(self, client_id: str) -> Optional[str]:
        """Get FCM token for a client."""
        self.ensure_clients_table_exists()
        cursor = self.conn.cursor()
        cursor.execute("SELECT fcm_token FROM clients WHERE client_id = ?", (client_id,))
        row = cursor.fetchone()
        return row["fcm_token"] if row else None

    def delete_client_token(self, client_id: str) -> bool:
        """Delete FCM token for a client. Returns True if deleted, False if not found."""
        self.ensure_clients_table_exists()
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM clients WHERE client_id = ?", (client_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def get_notifications_by_performance_ak(self, performance_ak: str) -> List[Dict[str, Any]]:
        """Get all notifications for a specific performance across all clients."""
        self.ensure_table_exists()
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM notifications WHERE performance_ak = ?", (performance_ak,))
        return [self._dict_from_row(row) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the database connection."""
        if self.conn:
            self.conn.close()
