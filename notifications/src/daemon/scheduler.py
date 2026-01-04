"""Daemon scheduler for periodic availability checks."""
import logging
import signal
import sys
import time

import schedule

from src.daemon.checker import check_availability
from src.storage.dynamodb import DynamoDBStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

storage = DynamoDBStorage()


def run_check():
    """Run the availability check."""
    logger.info("Running scheduled availability check")
    check_availability(storage)


def signal_handler(sig, frame):
    """Handle shutdown signals."""
    logger.info("Received shutdown signal, exiting...")
    sys.exit(0)


def main():
    """Main daemon entry point."""
    logger.info("Starting notifications daemon")

    # Ensure table exists
    try:
        storage.ensure_table_exists()
        logger.info("DynamoDB table ready")
    except Exception as e:
        logger.error(f"Failed to initialize DynamoDB table: {e}")
        sys.exit(1)

    # Schedule the check to run every 10 minutes
    schedule.every(10).minutes.do(run_check)

    # Run an initial check
    logger.info("Running initial availability check")
    run_check()

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Main loop
    logger.info("Daemon started, checking every 10 minutes")
    while True:
        schedule.run_pending()
        time.sleep(60)  # Check every minute for pending scheduled tasks


if __name__ == "__main__":
    main()

