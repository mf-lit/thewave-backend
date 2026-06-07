import logging
import uuid as uuid_module
from datetime import datetime, timezone

from src.core.water_temp_db import get_db_connection

logger = logging.getLogger(__name__)


def init_client_tracking() -> None:
    """Create the clients table if it doesn't exist."""
    with get_db_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                uuid TEXT PRIMARY KEY,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                request_count INTEGER NOT NULL DEFAULT 0,
                days_count INTEGER NOT NULL DEFAULT 1
            )
        """)
        try:
            conn.execute("ALTER TABLE clients ADD COLUMN days_count INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE clients ADD COLUMN client_os TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE clients ADD COLUMN client_version TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE clients ADD COLUMN first_ip TEXT")
        except Exception:
            pass
        try:
            conn.execute("ALTER TABLE clients ADD COLUMN last_ip TEXT")
        except Exception:
            pass
        # Migrate old hyphenated column names to underscores
        try:
            conn.execute('UPDATE clients SET client_os = "client-os" WHERE client_os IS NULL AND "client-os" IS NOT NULL')
            conn.execute('ALTER TABLE clients DROP COLUMN "client-os"')
        except Exception:
            pass
        try:
            conn.execute('UPDATE clients SET client_version = "client-version" WHERE client_version IS NULL AND "client-version" IS NOT NULL')
            conn.execute('ALTER TABLE clients DROP COLUMN "client-version"')
        except Exception:
            pass
        logger.info("Client tracking table initialized")


def _is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid_module.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def track_client(
    client_uuid: str,
    client_os: str | None = None,
    client_version: str | None = None,
    client_ip: str | None = None,
) -> None:
    """Upsert a client record, setting first_seen/first_ip on insert and updating last_seen/last_ip."""
    if not _is_valid_uuid(client_uuid):
        logger.warning(f"Ignoring invalid client UUID: {client_uuid}")
        return

    now = datetime.now(timezone.utc).isoformat()

    with get_db_connection() as conn:
        conn.execute("""
            INSERT INTO clients (uuid, first_seen, last_seen, request_count, days_count, client_os, client_version, first_ip, last_ip)
            VALUES (?, ?, ?, 1, 1, ?, ?, ?, ?)
            ON CONFLICT(uuid) DO UPDATE SET
                last_seen = excluded.last_seen,
                request_count = request_count + 1,
                days_count = days_count + CASE
                    WHEN date(clients.last_seen) != date(excluded.last_seen) THEN 1
                    ELSE 0
                END,
                client_os = COALESCE(excluded.client_os, clients.client_os),
                client_version = COALESCE(excluded.client_version, clients.client_version),
                first_ip = COALESCE(clients.first_ip, excluded.first_ip),
                last_ip = COALESCE(excluded.last_ip, clients.last_ip)
        """, (client_uuid, now, now, client_os, client_version, client_ip, client_ip))
    logger.info(f"Tracked client: {client_uuid}")
