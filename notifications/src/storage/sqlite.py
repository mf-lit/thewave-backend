"""SQLite storage layer for notifications."""
import json
import os
import sqlite3
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional


class SQLiteStorage:
    """Handles all SQLite operations for notifications."""

    def __init__(self, db_path: str = None):
        """Initialize SQLite connection.

        Args:
            db_path: Path to the SQLite database file (defaults to SQLITE_DB_PATH env var or 'notifications.db')
        """
        if db_path is None:
            db_path = os.getenv("SQLITE_DB_PATH", "notifications.db")
        self.db_path = db_path
        self.conn = None
        self._connect()

    def _connect(self):
        """Establish database connection."""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row  # Enable column access by name
        # Enable foreign keys
        self.conn.execute("PRAGMA foreign_keys = ON")

    def _dict_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        """Convert a SQLite Row to a dictionary.

        Args:
            row: SQLite Row object

        Returns:
            Dictionary representation
        """
        if row is None:
            return None
        result = dict(row)
        # Parse JSON fields
        if "thresholds" in result and result["thresholds"]:
            result["thresholds"] = json.loads(result["thresholds"])
        if "notified_thresholds" in result and result["notified_thresholds"]:
            result["notified_thresholds"] = json.loads(result["notified_thresholds"])
        return result

    def ensure_table_exists(self):
        """Create the notifications table if it doesn't exist."""
        cursor = self.conn.cursor()

        # Create notifications table
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

        # Create index on performance_ak for efficient lookups
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_performance_ak
            ON notifications(performance_ak)
        """)

        # Create index on date for efficient date-based queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_date
            ON notifications(date)
        """)

        self.conn.commit()

    def ensure_clients_table_exists(self):
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

    def create_notification(
        self, client_id: str, notification_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a new notification.

        Args:
            client_id: UUID of the client
            notification_data: Notification data (date, time, side, etc.)

        Returns:
            Created notification with notification_id
        """
        self.ensure_table_exists()

        notification_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        cursor = self.conn.cursor()

        # Prepare thresholds as JSON
        thresholds_json = None
        notified_thresholds_json = None
        if notification_data["notification_type"] == "below_threshold":
            thresholds_json = json.dumps(notification_data["thresholds"])
            notified_thresholds_json = json.dumps([])

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

        # Return the created item
        item = {
            "client_id": client_id,
            "notification_id": notification_id,
            "performance_ak": notification_data["performance_ak"],
            "date": notification_data["date"],
            "time": notification_data["time"],
            "side": notification_data["side"],
            "title": notification_data["title"],
            "notification_type": notification_data["notification_type"],
            "last_checked_availability": notification_data.get(
                "last_checked_availability"
            ),
            "created_at": now,
        }

        if notification_data["notification_type"] == "below_threshold":
            item["thresholds"] = notification_data["thresholds"]
            item["notified_thresholds"] = []

        return item

    def get_notifications_by_client(self, client_id: str) -> List[Dict[str, Any]]:
        """Get all notifications for a specific client.

        Args:
            client_id: UUID of the client

        Returns:
            List of notifications
        """
        self.ensure_table_exists()

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM notifications WHERE client_id = ?", (client_id,)
        )

        rows = cursor.fetchall()
        return [self._dict_from_row(row) for row in rows]

    def get_notification_by_id(
        self, client_id: str, notification_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a specific notification by ID.

        Args:
            client_id: UUID of the client
            notification_id: UUID of the notification

        Returns:
            Notification if found, None otherwise
        """
        self.ensure_table_exists()

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM notifications WHERE client_id = ? AND notification_id = ?",
            (client_id, notification_id),
        )

        row = cursor.fetchone()
        return self._dict_from_row(row) if row else None

    def delete_notification(self, client_id: str, notification_id: str) -> bool:
        """Delete a notification.

        Args:
            client_id: UUID of the client
            notification_id: UUID of the notification

        Returns:
            True if deleted, False if not found
        """
        self.ensure_table_exists()

        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM notifications WHERE client_id = ? AND notification_id = ?",
            (client_id, notification_id),
        )
        self.conn.commit()

        return cursor.rowcount > 0

    def update_notification(
        self, client_id: str, notification_id: str, updates: Dict[str, Any]
    ) -> bool:
        """Update a notification.

        Args:
            client_id: UUID of the client
            notification_id: UUID of the notification
            updates: Dictionary of fields to update

        Returns:
            True if updated, False if not found
        """
        self.ensure_table_exists()

        if not updates:
            return False

        # Build SET clause dynamically
        set_clauses = []
        values = []

        for key, value in updates.items():
            # Handle list fields that need JSON serialization
            if key in ["thresholds", "notified_thresholds"] and isinstance(value, list):
                value = json.dumps(value)
            set_clauses.append(f"{key} = ?")
            values.append(value)

        # Add WHERE clause values
        values.extend([client_id, notification_id])

        cursor = self.conn.cursor()
        query = f"UPDATE notifications SET {', '.join(set_clauses)} WHERE client_id = ? AND notification_id = ?"
        cursor.execute(query, values)
        self.conn.commit()

        return cursor.rowcount > 0

    def get_all_notifications(self) -> List[Dict[str, Any]]:
        """Get all notifications across all clients (for daemon use).

        Returns:
            List of all notifications
        """
        self.ensure_table_exists()

        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM notifications")

        rows = cursor.fetchall()
        return [self._dict_from_row(row) for row in rows]

    def create_or_update_client_token(
        self, client_id: str, fcm_token: str
    ) -> Dict[str, Any]:
        """Create or update FCM token for a client.

        Args:
            client_id: UUID of the client
            fcm_token: Firebase Cloud Messaging token

        Returns:
            Client record with fcm_token and updated_at
        """
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
        """Get FCM token for a client.

        Args:
            client_id: UUID of the client

        Returns:
            FCM token string if found, None otherwise
        """
        self.ensure_clients_table_exists()

        cursor = self.conn.cursor()
        cursor.execute("SELECT fcm_token FROM clients WHERE client_id = ?", (client_id,))

        row = cursor.fetchone()
        return row["fcm_token"] if row else None

    def delete_client_token(self, client_id: str) -> bool:
        """Delete FCM token for a client.

        Args:
            client_id: UUID of the client

        Returns:
            True if deleted, False if not found
        """
        self.ensure_clients_table_exists()

        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM clients WHERE client_id = ?", (client_id,))
        self.conn.commit()

        return cursor.rowcount > 0

    def get_notifications_by_performance_ak(
        self, performance_ak: str
    ) -> List[Dict[str, Any]]:
        """Get all notifications for a specific performance across all clients.

        Args:
            performance_ak: Performance identifier

        Returns:
            List of notifications
        """
        self.ensure_table_exists()

        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM notifications WHERE performance_ak = ?", (performance_ak,)
        )

        rows = cursor.fetchall()
        return [self._dict_from_row(row) for row in rows]

    def close(self):
        """Close the database connection."""
        if self.conn:
            self.conn.close()
