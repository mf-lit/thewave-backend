import sqlite3
import logging
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
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

# Stuck-sensor detection: the upstream sensor occasionally freezes, repeating the
# same reading indefinitely. We treat the reading as stuck once it has not changed
# for this many hours. Detection only needs a recent window; reporting the true
# onset needs a wider one (see below).
STUCK_THRESHOLD_HOURS = 6
STUCK_DETECTION_WINDOW_HOURS = 48

# Once a freeze is detected, re-evaluate over this much wider lookback so the
# reported "since"/"hours" reflect the real start of the frozen run rather than
# the detection-window edge. Kept separate from (and larger than) the detection
# window so the deep query is paid only in the rare stuck case, not on every
# (usually not-stuck) call. A freeze older than this is still capped here.
STUCK_ONSET_LOOKBACK_HOURS = int(os.getenv("STUCK_ONSET_LOOKBACK_HOURS", str(24 * 30)))

# Stored timestamps are UTC; the "since" reported to clients is localized to
# Europe/London to match the rest of the user-facing timing in this service.
LONDON_TZ = ZoneInfo("Europe/London")


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
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                valid INTEGER NOT NULL DEFAULT 1
            )
        """)

        # Create index on recorded_at for faster queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_recorded_at
            ON water_temperature(recorded_at)
        """)

        # Migrate pre-existing tables that predate the `valid` column. Adding it
        # with DEFAULT 1 marks every existing row valid, so backfill once to
        # apply the stuck-run rule to historical data.
        columns = {row["name"] for row in cursor.execute("PRAGMA table_info(water_temperature)")}
        if "valid" not in columns:
            logger.info("Adding `valid` column to water_temperature and backfilling")
            cursor.execute("ALTER TABLE water_temperature ADD COLUMN valid INTEGER NOT NULL DEFAULT 1")
            _backfill_validity(conn)

        conn.commit()
        logger.info(f"Water temperature database initialized at {DB_PATH}")


def store_water_temperature(temperature: float, recorded_at: datetime | None = None) -> int:
    """
    Store a water temperature reading in the database.

    Timestamps are persisted as naive UTC (no offset), independent of the host
    timezone. Upstream performance times are Europe/London local, so the read
    side (performance_temperature) converts London->UTC before matching against
    these rows. Storing UTC also avoids the DST fall-back duplicate-hour
    ambiguity that local time would introduce.

    Args:
        temperature: Water temperature in degrees
        recorded_at: Timestamp when the temperature was recorded. If None, uses
            the current UTC time. A tz-aware value is converted to UTC; a naive
            value is assumed to already be UTC.

    Returns:
        int: ID of the inserted record
    """
    if recorded_at is None:
        recorded_at = datetime.now(timezone.utc)

    # Normalise to naive UTC for a consistent, offset-free stored representation.
    if recorded_at.tzinfo is not None:
        recorded_at = recorded_at.astimezone(timezone.utc).replace(tzinfo=None)

    recorded_at_str = recorded_at.isoformat()

    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO water_temperature (temperature, recorded_at)
            VALUES (?, ?)
        """, (temperature, recorded_at_str))

        record_id = cursor.lastrowid

        # Maintain the `valid` flag for the run this reading belongs to. If the
        # sensor has now been frozen on one value for >= STUCK_THRESHOLD_HOURS,
        # this also retroactively invalidates the mid-run rows written earlier.
        _update_validity_for_new_reading(conn, recorded_at_str, temperature)

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
            SELECT id, temperature, recorded_at, created_at, valid
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
                "created_at": row["created_at"],
                "valid": bool(row["valid"])
            }
        return None


def get_latest_valid_temperature() -> dict | None:
    """
    Get the most recent water temperature reading that is not part of a frozen
    (stuck-sensor) run — i.e. the newest reading with ``valid = 1``.

    Used as the safety-net fallback so a carried-forward estimate is never sourced
    from a stuck reading.

    Returns:
        dict | None: Dictionary with temperature, recorded_at, created_at, valid,
            or None if there is no valid reading.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, temperature, recorded_at, created_at, valid
            FROM water_temperature
            WHERE valid = 1
            ORDER BY recorded_at DESC
            LIMIT 1
        """)

        row = cursor.fetchone()
        if row:
            return {
                "id": row["id"],
                "temperature": row["temperature"],
                "recorded_at": row["recorded_at"],
                "created_at": row["created_at"],
                "valid": bool(row["valid"])
            }
        return None


def is_reading_valid(recorded_at: str) -> bool:
    """
    Return the *current* stuck-sensor validity of the reading stored at
    ``recorded_at``.

    Read live (uncached) on purpose: the ``valid`` flag is mutated retroactively
    when a frozen run is detected (see _update_validity_for_new_reading), so a
    reading that was valid when first looked up can later become invalid. Callers
    that cache the reading's immutable facts (temperature, recorded_at) must route
    the mutable flag through here to avoid serving stale validity.

    Args:
        recorded_at: The exact stored recorded_at string of the reading (unique to
            microsecond precision).

    Returns:
        bool: The reading's ``valid`` flag, or False if no such row exists.
    """
    with get_db_connection() as conn:
        row = conn.execute(
            "SELECT valid FROM water_temperature WHERE recorded_at = ? LIMIT 1",
            (recorded_at,),
        ).fetchone()
        return bool(row["valid"]) if row is not None else False


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
            SELECT id, temperature, recorded_at, created_at, valid
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
                "created_at": row["created_at"],
                "valid": bool(row["valid"])
            }
            for row in rows
        ]


def _parse_recorded_at(recorded_at: str) -> datetime:
    """
    Parse a stored recorded_at value (naive UTC ISO-8601) into a UTC-aware datetime.
    """
    parsed = datetime.fromisoformat(recorded_at)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _update_validity_for_new_reading(conn: sqlite3.Connection, recorded_at_str: str,
                                     temperature: float) -> None:
    """
    Update the `valid` flag for the frozen-value run that the just-inserted
    reading belongs to.

    A "run" is a maximal sequence of consecutive readings (by recorded_at) with
    the same temperature. Once a run's span reaches STUCK_THRESHOLD_HOURS the
    first reading stays valid and every later reading in the run is invalid.

    We anchor on the run boundary rather than re-scanning a bounded window so
    this stays correct for runs longer than the detection window, and we only
    ever flip valid 1 -> 0 (validity is monotonic within a run). The first
    reading's flag is never touched here — it was written valid when the value
    last changed and remains so.

    Args:
        conn: Open connection (the same transaction as the insert).
        recorded_at_str: recorded_at of the new reading (naive-UTC ISO string).
        temperature: The new reading's value.
    """
    cursor = conn.cursor()

    # Boundary = the most recent earlier reading with a *different* value. Every
    # row after it (up to and including the new one) shares the new value.
    boundary = cursor.execute(
        """
        SELECT recorded_at FROM water_temperature
        WHERE recorded_at < ? AND temperature != ?
        ORDER BY recorded_at DESC LIMIT 1
        """,
        (recorded_at_str, temperature),
    ).fetchone()

    # run_start = earliest reading after the boundary (or the earliest overall
    # when the whole table is one unbroken value).
    if boundary is not None:
        run_start = cursor.execute(
            """
            SELECT recorded_at FROM water_temperature
            WHERE recorded_at > ? ORDER BY recorded_at ASC LIMIT 1
            """,
            (boundary["recorded_at"],),
        ).fetchone()
    else:
        run_start = cursor.execute(
            "SELECT recorded_at FROM water_temperature ORDER BY recorded_at ASC LIMIT 1"
        ).fetchone()

    if run_start is None:
        return

    span_hours = (
        _parse_recorded_at(recorded_at_str) - _parse_recorded_at(run_start["recorded_at"])
    ).total_seconds() / 3600

    if span_hours < STUCK_THRESHOLD_HOURS:
        return

    # Invalidate every run member after the first: the mid-run rows written
    # before detection and the new reading, in one statement.
    cursor.execute(
        """
        UPDATE water_temperature SET valid = 0
        WHERE recorded_at > ? AND recorded_at <= ?
        """,
        (run_start["recorded_at"], recorded_at_str),
    )


def _backfill_validity(conn: sqlite3.Connection) -> None:
    """
    One-time pass over the whole table applying the stuck-run validity rule, used
    when the `valid` column is first added. Groups consecutive equal-temperature
    readings into runs and invalidates every row except the first in any run
    whose span reaches STUCK_THRESHOLD_HOURS.
    """
    cursor = conn.cursor()
    rows = cursor.execute(
        "SELECT id, temperature, recorded_at FROM water_temperature ORDER BY recorded_at ASC"
    ).fetchall()

    invalid_ids: list[int] = []
    run: list[sqlite3.Row] = []

    def flush(run_rows: list[sqlite3.Row]) -> None:
        if len(run_rows) < 2:
            return
        span_hours = (
            _parse_recorded_at(run_rows[-1]["recorded_at"])
            - _parse_recorded_at(run_rows[0]["recorded_at"])
        ).total_seconds() / 3600
        if span_hours >= STUCK_THRESHOLD_HOURS:
            invalid_ids.extend(r["id"] for r in run_rows[1:])

    for row in rows:
        if run and row["temperature"] != run[-1]["temperature"]:
            flush(run)
            run = []
        run.append(row)
    flush(run)

    if invalid_ids:
        cursor.executemany(
            "UPDATE water_temperature SET valid = 0 WHERE id = ?",
            [(i,) for i in invalid_ids],
        )
        logger.info(f"Backfilled validity: marked {len(invalid_ids)} stuck readings invalid")


def _not_stuck() -> dict:
    return {"stuck": False, "since": None, "hours": None}


def _compute_stuck_status(readings: list[dict], now: datetime,
                          threshold_hours: float = STUCK_THRESHOLD_HOURS) -> dict:
    """
    Determine whether the water-temperature sensor is stuck (frozen on one value).

    Args:
        readings: Temperature records newest-first (as returned by
            get_temperature_history), each with "temperature" and "recorded_at".
        now: Current UTC-aware time (unused for the span but kept for clarity/extension).
        threshold_hours: Unchanged at least this long => stuck.

    Returns:
        dict: {"stuck": bool, "since": ISO-8601 str | None, "hours": float | None}.
        When not stuck, "since" and "hours" are None.
    """
    if len(readings) < 2:
        return _not_stuck()

    latest_value = readings[0]["temperature"]

    # Walk forward (back in time) while the value matches; the last matching row
    # is the start of the frozen run. Span-based so it tolerates gaps and
    # duplicate rows within an hour.
    run_start = readings[0]
    for reading in readings[1:]:
        if reading["temperature"] != latest_value:
            break
        run_start = reading

    latest_time = _parse_recorded_at(readings[0]["recorded_at"])
    run_start_time = _parse_recorded_at(run_start["recorded_at"])
    span_hours = (latest_time - run_start_time).total_seconds() / 3600

    if span_hours < threshold_hours:
        return _not_stuck()

    return {
        "stuck": True,
        "since": run_start_time.astimezone(LONDON_TZ).isoformat(),
        "hours": round(span_hours, 1),
    }


def get_stuck_sensor_status() -> dict:
    """
    Check the recent temperature history and report whether the sensor is stuck.

    Reads from the DB independently of the live weather cache. Any failure
    degrades to a not-stuck result rather than propagating, so callers
    (e.g. the /wave-weather endpoint) never fail solely on this check.

    Detection runs over the recent STUCK_DETECTION_WINDOW_HOURS window. When a
    freeze is found, the run is re-evaluated over the wider
    STUCK_ONSET_LOOKBACK_HOURS lookback so the reported "since"/"hours" reflect
    the true start of the frozen run rather than the detection-window edge.

    Returns:
        dict: {"stuck": bool, "since": ISO-8601 str | None, "hours": float | None}.
    """
    try:
        now = datetime.now(timezone.utc)
        window_start = (now - timedelta(hours=STUCK_DETECTION_WINDOW_HOURS))
        # Stored recorded_at is naive UTC, so compare against a naive-UTC string.
        start_date = window_start.replace(tzinfo=None).isoformat()
        readings = get_temperature_history(limit=200, start_date=start_date)
        status = _compute_stuck_status(readings, now)
        if not status["stuck"]:
            return status

        # Stuck: the recent window may have truncated the run, so re-evaluate
        # over a much wider lookback to find the true onset. Hourly readings mean
        # ~1 row/hour; allow generous headroom for duplicate rows within an hour.
        onset_start = (
            (now - timedelta(hours=STUCK_ONSET_LOOKBACK_HOURS))
            .replace(tzinfo=None)
            .isoformat()
        )
        deep_readings = get_temperature_history(
            limit=STUCK_ONSET_LOOKBACK_HOURS * 4,
            start_date=onset_start,
        )
        return _compute_stuck_status(deep_readings, now)
    except Exception as e:
        logger.warning(f"Stuck-sensor detection failed: {e}")
        return _not_stuck()
