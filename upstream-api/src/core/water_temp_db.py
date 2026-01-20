import sqlite3
import logging
import os
from datetime import datetime
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Database file path
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
    "water_temperature.db"
)

# Database timeout in seconds (how long to wait for a lock)
DB_TIMEOUT = float(os.getenv("DB_TIMEOUT", "30.0"))


@contextmanager
def get_db_connection():
    """
    Context manager for database connections.
    Ensures proper connection cleanup.

    Uses WAL mode for better concurrency (allows readers during writes)
    and configurable timeout for lock contention.

    Yields:
        sqlite3.Connection: Database connection
    """
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for better concurrent access
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database() -> None:
    """
    Initialize the water temperature database.
    Creates the table if it doesn't exist.
    """
    # Ensure data directory exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS water_temperature (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                temperature REAL NOT NULL,
                recorded_at TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create index on recorded_at for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_recorded_at
            ON water_temperature(recorded_at)
        """)

        conn.commit()
        logger.info(f"Water temperature database initialized at {DB_PATH}")


def store_water_temperature(temperature: float, recorded_at: datetime | None = None) -> int:
    """
    Store a water temperature reading in the database.

    Args:
        temperature: Water temperature in degrees
        recorded_at: Timestamp when the temperature was recorded. If None, uses current time.

    Returns:
        int: ID of the inserted record
    """
    if recorded_at is None:
        recorded_at = datetime.now()

    recorded_at_str = recorded_at.isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO water_temperature (temperature, recorded_at)
            VALUES (?, ?)
        """, (temperature, recorded_at_str))

        record_id = cursor.lastrowid
        logger.info(f"Stored water temperature: {temperature}°C at {recorded_at_str} (ID: {record_id})")
        return record_id


def get_latest_temperature() -> dict | None:
    """
    Get the most recent water temperature reading.

    Returns:
        dict | None: Dictionary with temperature, recorded_at, and created_at, or None if no data
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, temperature, recorded_at, created_at
            FROM water_temperature
            ORDER BY recorded_at DESC
            LIMIT 1
        """)

        row = cursor.fetchone()
        if row:
            return {
                "id": row["id"],
                "temperature": row["temperature"],
                "recorded_at": row["recorded_at"],
                "created_at": row["created_at"]
            }
        return None


def get_temperature_history(limit: int = 100, start_date: str | None = None, end_date: str | None = None) -> list[dict]:
    """
    Get historical water temperature readings.

    Args:
        limit: Maximum number of records to return (default: 100)
        start_date: Optional start date in ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)
        end_date: Optional end date in ISO format (YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS)

    Returns:
        list[dict]: List of temperature records, newest first
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        query = """
            SELECT id, temperature, recorded_at, created_at
            FROM water_temperature
            WHERE 1=1
        """
        params = []

        if start_date:
            query += " AND recorded_at >= ?"
            params.append(start_date)

        if end_date:
            query += " AND recorded_at <= ?"
            params.append(end_date)

        query += " ORDER BY recorded_at DESC LIMIT ?"
        params.append(limit)

        cursor.execute(query, params)

        rows = cursor.fetchall()
        return [
            {
                "id": row["id"],
                "temperature": row["temperature"],
                "recorded_at": row["recorded_at"],
                "created_at": row["created_at"]
            }
            for row in rows
        ]
