"""Daemon scheduler for periodic availability checks."""
import logging
import signal
import sys
import time

import schedule

from src.daemon.checker import check_availability
from src.storage.sqlite import SQLiteStorage

CHECK_INTERVAL_MINUTES = 3
SLEEP_INTERVAL_SECONDS = 60

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

storage = SQLiteStorage()


def run_check() -> None:
    """Run the availability check."""
    logger.info("Running scheduled availability check")
    check_availability(storage)


def signal_handler(sig, frame) -> None:
    """Handle shutdown signals gracefully."""
    logger.info("Received shutdown signal, exiting...")
    sys.exit(0)


def main() -> None:
    """Main daemon entry point."""
    logger.info("Starting notifications daemon")

    try:
        storage.ensure_table_exists()
        logger.info("SQLite database ready")
    except Exception as e:
        logger.error(f"Failed to initialize SQLite database: {e}")
        sys.exit(1)

    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(run_check)

    logger.info("Running initial availability check")
    run_check()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    logger.info(f"Daemon started, checking every {CHECK_INTERVAL_MINUTES} minutes")
    while True:
        schedule.run_pending()
        time.sleep(SLEEP_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()

