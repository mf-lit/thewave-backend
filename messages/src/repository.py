"""Data access.

Every statement is parameterised and written out in full — no ``UPDATE``
assembled from whichever keys a caller happened to pass, which is how the set
of writable columns stops being knowable by reading the code.

The read path is deliberately unclever: ``list_enabled`` loads every enabled
row and ``targeting`` decides the rest in Python. See the note there for why
the filtering does not belong in SQL.
"""
from __future__ import annotations

import logging
import uuid
from typing import Dict, Iterable, List, Optional, Tuple

from . import clock
from .db import Database
from .models import Message, MessageRequest, encode_list

logger = logging.getLogger(__name__)

_COLUMNS = """
    message_id, title, body, client_ids, os, min_version, max_version,
    min_days_count, max_days_count, starts_at, ends_at, retain, expires_at,
    display, level, priority, action_url, action_label, enabled, revision,
    created_at, updated_at, dismissable_at, dismissable_after
"""

_SELECT = f"SELECT {_COLUMNS} FROM messages"


def _authored_values(request: MessageRequest) -> Tuple:
    """The columns an admin writes, in the order both statements name them.

    Shared by ``create`` and ``update`` so the two can never drift into
    disagreeing about what a message is made of.
    """
    return (
        request.title,
        request.body,
        encode_list(request.client_ids),
        encode_list(request.os),
        request.min_version,
        request.max_version,
        request.min_days_count,
        request.max_days_count,
        request.starts_at,
        request.ends_at,
        int(request.retain),
        request.expires_at,
        request.display,
        request.level,
        request.priority,
        request.action_url,
        request.action_label,
        int(request.enabled),
        request.dismissable_at,
        request.dismissable_after,
    )


class MessageRepository:
    def __init__(self, database: Database):
        self.db = database

    # ------------------------------------------------------------------ reads

    def list_enabled(self) -> List[Message]:
        """Every enabled row, for a client request to be filtered from."""
        rows = self.db.connection().execute(
            f"{_SELECT} WHERE enabled = 1"
        ).fetchall()
        return [Message.from_row(row) for row in rows]

    def list_all(self) -> List[Message]:
        """Every row, newest first, for the admin list."""
        rows = self.db.connection().execute(
            f"{_SELECT} ORDER BY created_at DESC, message_id"
        ).fetchall()
        return [Message.from_row(row) for row in rows]

    def get(self, message_id: str) -> Optional[Message]:
        row = self.db.connection().execute(
            f"{_SELECT} WHERE message_id = ?", (message_id,)
        ).fetchone()
        return Message.from_row(row) if row else None

    # ----------------------------------------------------------------- writes

    def create(self, request: MessageRequest) -> Message:
        message_id = str(uuid.uuid4())
        now = clock.utc_now_iso()

        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO messages (
                    message_id, title, body, client_ids, os, min_version,
                    max_version, min_days_count, max_days_count, starts_at,
                    ends_at, retain, expires_at, display, level, priority,
                    action_url, action_label, enabled, dismissable_at,
                    dismissable_after, revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (message_id, *_authored_values(request), now, now),
            )

        logger.info("Created message %s (%s)", message_id, request.display)
        return self.get(message_id)

    def update(
        self, message_id: str, request: MessageRequest, bump_revision: bool = False
    ) -> Optional[Message]:
        """Rewrite every authored column. Returns None if there is no such row.

        ``revision`` moves only when asked. Editing a typo should not re-show a
        modal to everyone who has already dismissed it; changing what the
        message *says* should. That is a judgement about the edit, so it is the
        caller's to make and not something inferred from a diff.
        """
        now = clock.utc_now_iso()

        with self.db.transaction() as conn:
            cursor = conn.execute(
                """
                UPDATE messages SET
                    title = ?, body = ?, client_ids = ?, os = ?,
                    min_version = ?, max_version = ?, min_days_count = ?,
                    max_days_count = ?, starts_at = ?, ends_at = ?, retain = ?,
                    expires_at = ?, display = ?, level = ?, priority = ?,
                    action_url = ?, action_label = ?, enabled = ?,
                    dismissable_at = ?, dismissable_after = ?,
                    revision = revision + ?, updated_at = ?
                WHERE message_id = ?
                """,
                (*_authored_values(request), int(bump_revision), now, message_id),
            )
            updated = cursor.rowcount

        if not updated:
            return None
        logger.info("Updated message %s (revision bumped: %s)", message_id, bump_revision)
        return self.get(message_id)

    def set_enabled(self, message_id: str, enabled: bool) -> Optional[Message]:
        """The kill switch. Separate from ``update`` so retracting is one call.

        No revision bump: re-enabling a message shows it to whoever had not yet
        acked it, and to nobody who had.
        """
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "UPDATE messages SET enabled = ?, updated_at = ? WHERE message_id = ?",
                (int(enabled), clock.utc_now_iso(), message_id),
            )
            updated = cursor.rowcount

        if not updated:
            return None
        logger.info("Set message %s enabled=%s", message_id, enabled)
        return self.get(message_id)

    def delete(self, message_id: str) -> bool:
        """Remove a message and the acks for it.

        The acks go too: they are only ever read as "has this client seen this
        message", so once the message is gone they are dead weight on a table
        that grows with every client. There is no foreign key doing this — the
        two tables are written independently and a constraint would make an ack
        for a since-deleted message an error rather than a no-op.
        """
        with self.db.transaction() as conn:
            cursor = conn.execute(
                "DELETE FROM messages WHERE message_id = ?", (message_id,)
            )
            deleted = cursor.rowcount
            conn.execute("DELETE FROM message_acks WHERE message_id = ?", (message_id,))

        if deleted:
            logger.info("Deleted message %s", message_id)
        return bool(deleted)

    # ------------------------------------------------------------------- acks

    def acked_revisions(self, client_id: str) -> Dict[str, int]:
        """What this client has already seen: message_id -> acked revision."""
        rows = self.db.connection().execute(
            "SELECT message_id, revision FROM message_acks WHERE client_id = ?",
            (client_id,),
        ).fetchall()
        return {row["message_id"]: row["revision"] for row in rows}

    def record_acks(
        self, client_id: str, acks: Iterable[Tuple[str, int]]
    ) -> int:
        """Upsert one client's acks. Returns how many were recorded.

        An ack for a message that no longer exists is skipped rather than
        rejected, so a client holding a since-deleted message can still flush
        its queue instead of retrying the same failing batch forever. The
        ``WHERE EXISTS`` is what makes that a no-op rather than an orphan row.
        """
        now = clock.utc_now_iso()
        recorded = 0

        with self.db.transaction() as conn:
            for message_id, revision in acks:
                cursor = conn.execute(
                    """
                    INSERT INTO message_acks (message_id, client_id, revision, acked_at)
                    SELECT ?, ?, ?, ?
                    WHERE EXISTS (SELECT 1 FROM messages WHERE message_id = ?)
                    ON CONFLICT(message_id, client_id) DO UPDATE SET
                        revision = excluded.revision,
                        acked_at = excluded.acked_at
                    """,
                    (message_id, client_id, revision, now, message_id),
                )
                recorded += cursor.rowcount

        return recorded

    def ack_counts(self) -> Dict[str, int]:
        """How many clients have acked each message, for the admin list."""
        rows = self.db.connection().execute(
            "SELECT message_id, COUNT(*) AS acks FROM message_acks GROUP BY message_id"
        ).fetchall()
        return {row["message_id"]: row["acks"] for row in rows}
