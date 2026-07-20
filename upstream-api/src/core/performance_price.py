import sqlite3
import logging
import os
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Database file path (lives alongside the other app databases in data/)
DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data",
    "prices.db",
)

# Database timeout in seconds (how long to wait for a lock)
DB_TIMEOUT = float(os.getenv("DB_TIMEOUT", "30.0"))


@contextmanager
def get_db_connection():
    """
    Context manager for read-only connections to the price database.

    Opened read-only (mode=ro) because this service only consumes price
    overrides; it never writes them. read-only mode also means we never
    create an empty price.db if the file is missing -- the connect call
    raises instead, which callers treat as "no overrides available".

    Yields:
        sqlite3.Connection: Database connection
    """
    conn = sqlite3.connect(
        f"file:{DB_PATH}?mode=ro", uri=True, timeout=DB_TIMEOUT
    )
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _get_price_overrides(performance_aks: list) -> dict:
    """
    Look up more accurate price values for the given performance AKs.

    Args:
        performance_aks: performanceAK identifiers to look up

    Returns:
        dict: performanceAK -> (price_min, price_max) for AKs present in the
        database. AKs absent from price.db are simply omitted. Returns an empty
        dict if the database is unavailable.
    """
    if not performance_aks:
        return {}

    try:
        with get_db_connection() as conn:
            placeholders = ",".join("?" for _ in performance_aks)
            rows = conn.execute(
                f"SELECT performanceAK, price_min, price_max FROM performances "
                f"WHERE performanceAK IN ({placeholders})",
                performance_aks,
            ).fetchall()
        return {
            row["performanceAK"]: (row["price_min"], row["price_max"])
            for row in rows
        }
    except sqlite3.OperationalError as e:
        # Missing file (mode=ro), missing table, or locked db: no overrides.
        logger.warning(f"Price override database unavailable: {e}")
        return {}
    except sqlite3.Error as e:
        logger.error(f"Error querying price override database: {e}")
        return {}


def add_prices_to_performances(data: dict) -> dict:
    """
    Override upstream priceMin/priceMax with more accurate values from price.db.

    The upstream API returns priceMin/priceMax for each performance, but price.db
    holds more accurate values for a subset of performances (keyed by
    performanceAK). Where a performance is present in price.db, its priceMin and
    priceMax are replaced; otherwise the upstream values are left untouched.

    Args:
        data: Calendar response data with days and performances

    Returns:
        dict: Modified data with overridden price fields where available
    """
    try:
        days = data.get("days", [])

        # Collect every performanceAK across all days so the database is
        # queried once per response rather than once per performance.
        performance_aks = [
            performance["performanceAK"]
            for day in days
            for performance in day.get("performances", [])
            if performance.get("performanceAK") is not None
        ]

        overrides = _get_price_overrides(performance_aks)
        if not overrides:
            return data

        applied = 0
        for day in days:
            for performance in day.get("performances", []):
                override = overrides.get(performance.get("performanceAK"))
                if override is not None:
                    price_min, price_max = override
                    performance["priceMin"] = price_min
                    performance["priceMax"] = price_max
                    applied += 1

        if applied:
            logger.info(f"Applied price overrides to {applied} performance(s)")

    except Exception as e:
        # On any unexpected error, log and return data unchanged so the
        # response still reflects upstream prices.
        logger.error(f"Critical error in add_prices_to_performances: {e}", exc_info=True)

    return data
