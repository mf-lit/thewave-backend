import os
import logging
from datetime import datetime, timedelta
from flask import Flask

logger = logging.getLogger(__name__)

# Global scheduler instance
_scheduler = None


def record_water_temperature():
    """
    Fetch current water temperature and store it in the database.
    This function is called hourly.
    """
    # Lazy imports to avoid circular import issues
    from src.core.weather import get_water_temperature
    from src.core.water_temp_db import store_water_temperature

    # Check if test mode is enabled (skip in test mode)
    test_mode = os.getenv("TEST_MODE", "").lower() in ("true", "1", "yes")
    if test_mode:
        logger.info("Skipping water temperature recording - test mode is enabled")
        return

    try:
        logger.info("Starting hourly water temperature recording")

        # Fetch current water temperature
        temperature = get_water_temperature()

        # Store in database
        record_id = store_water_temperature(temperature)

        logger.info(f"Successfully recorded water temperature: {temperature}°C (ID: {record_id})")

    except Exception as e:
        logger.error(f"Failed to record water temperature: {str(e)}", exc_info=True)


def _check_and_archive_day(date: str) -> bool:
    """
    Check if a historical file exists for a date, and if not, fetch and save it.

    Args:
        date: Date in YYYY-MM-DD format

    Returns:
        bool: True if file now exists (either already existed or was successfully created), False otherwise
    """
    # Lazy imports to avoid circular import issues
    from src.core.history import save_daily_history, load_historical_day
    from src.core.wave_calendar import get_calendar, add_side_to_availability

    # Check if file already exists
    if load_historical_day(date) is not None:
        logger.info(f"Historical file already exists for {date}, skipping")
        return True

    # File doesn't exist, try to fetch and save it
    try:
        logger.info(f"Missing historical file for {date}, attempting to fetch and save")
        response_data = get_calendar(date, "1")
        # Add side field before saving to history
        response_data = add_side_to_availability(response_data)
        save_daily_history(date, response_data)
        logger.info(f"Successfully archived missing historical data for {date}")
        return True
    except Exception as e:
        logger.error(f"Failed to archive historical data for {date}: {str(e)}", exc_info=True)
        return False


def archive_today_response():
    """
    Archive today's API response to history.
    Also checks and backfills the previous 6 days if any are missing.
    This function is called daily at 23:59.
    """
    # Lazy imports to avoid circular import issues
    from src.core.history import save_daily_history
    from src.core.wave_calendar import get_calendar, add_side_to_availability

    # Check if test mode is enabled (history only works in production)
    test_mode = os.getenv("TEST_MODE", "").lower() in ("true", "1", "yes")
    if test_mode:
        logger.info("Skipping daily archive - test mode is enabled")
        return

    try:
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")
        logger.info(f"Starting daily archive for {today_str}")

        # Fetch today's data from upstream API (single day)
        response_data = get_calendar(today_str, "1")

        # Add side field before saving to history
        response_data = add_side_to_availability(response_data)

        # Save to history
        save_daily_history(today_str, response_data)

        logger.info(f"Successfully archived daily response for {today_str}")
        
        # Check and backfill previous 6 days
        logger.info("Checking previous 6 days for missing historical files")
        missing_count = 0
        for day_offset in range(1, 7):  # Previous 1-6 days
            check_date = today - timedelta(days=day_offset)
            check_date_str = check_date.strftime("%Y-%m-%d")
            
            if not _check_and_archive_day(check_date_str):
                missing_count += 1
        
        if missing_count > 0:
            logger.warning(f"Failed to backfill {missing_count} out of 6 previous days")
        else:
            logger.info("All previous 6 days are now archived")
            
    except Exception as e:
        logger.error(f"Failed to archive daily response: {str(e)}", exc_info=True)


def setup_daily_archive_task(app: Flask):
    """
    Initialize APScheduler and schedule daily archive task at 23:59 and hourly water temperature recording.

    Args:
        app: Flask application instance
    """
    # Lazy import APScheduler to avoid circular import issues
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    from src.core.water_temp_db import init_database
    import atexit

    global _scheduler

    if _scheduler is not None:
        logger.warning("Scheduler already initialized, skipping setup")
        return

    # Initialize water temperature database
    init_database()

    _scheduler = BackgroundScheduler()

    # Schedule daily archive task to run at 23:59
    _scheduler.add_job(
        func=archive_today_response,
        trigger=CronTrigger(hour=23, minute=59),
        id="daily_archive",
        name="Daily archive of API response",
        replace_existing=True
    )

    # Schedule hourly water temperature recording at the top of each hour
    _scheduler.add_job(
        func=record_water_temperature,
        trigger=CronTrigger(minute=0),
        id="hourly_water_temp",
        name="Hourly water temperature recording",
        replace_existing=True
    )

    _scheduler.start()
    logger.info("Scheduler started:")
    logger.info("  - Daily archive runs at 23:59 each day")
    logger.info("  - Water temperature recording runs at the top of each hour")

    # Ensure scheduler shuts down when app exits
    atexit.register(lambda: _scheduler.shutdown() if _scheduler else None)
