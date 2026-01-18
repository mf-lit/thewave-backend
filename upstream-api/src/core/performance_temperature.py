import logging
import os
import csv
from datetime import datetime, timedelta
from typing import Literal

logger = logging.getLogger(__name__)

# Path to forecast CSV file
FORECAST_CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "latest.csv"
)


def _parse_performance_datetime(date_str: str, time_str: str) -> datetime:
    """
    Parse performance date and time into a datetime object.

    Args:
        date_str: Date in YYYY-MM-DD format
        time_str: Time in HH:MM:SS.mmm format

    Returns:
        datetime: Combined datetime object
    """
    # Remove milliseconds from time string (e.g., "10:00:00.000" -> "10:00:00")
    time_clean = time_str.split('.')[0]
    dt_str = f"{date_str} {time_clean}"
    return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")


def _get_performance_status(performance: dict, now: datetime) -> Literal["past", "current", "future"]:
    """
    Determine if a performance is in the past, happening now, or in the future.

    Args:
        performance: Performance dictionary with 'date', 'time', and 'timeEnd' fields
        now: Current datetime to compare against

    Returns:
        str: "past", "current", or "future"
    """
    date_str = performance.get("date")
    time_str = performance.get("time")
    time_end_str = performance.get("timeEnd")

    if not all([date_str, time_str, time_end_str]):
        logger.warning(f"Performance missing time fields: {performance.get('performanceAK', 'unknown')}")
        return "future"

    try:
        start_time = _parse_performance_datetime(date_str, time_str)
        end_time = _parse_performance_datetime(date_str, time_end_str)

        if now < start_time:
            return "future"
        elif now > end_time:
            return "past"
        else:
            return "current"
    except (ValueError, KeyError) as e:
        logger.error(f"Error parsing performance time: {e}")
        return "future"


def _get_temperature_for_time(target_time: datetime) -> float | None:
    """
    Get the water temperature for a specific time.
    Looks for the closest temperature reading in the database.

    Args:
        target_time: The time to get temperature for

    Returns:
        float | None: Temperature if found, None otherwise
    """
    from src.core.water_temp_db import get_temperature_history

    # Get temperatures within +/- 2 hours of target time
    start_time = target_time - timedelta(hours=2)
    end_time = target_time + timedelta(hours=2)

    temperatures = get_temperature_history(
        limit=10,
        start_date=start_time.isoformat(),
        end_date=end_time.isoformat()
    )

    if not temperatures:
        logger.debug(f"No temperature data found near {target_time.isoformat()}")
        return None

    # Find the closest temperature reading
    closest = min(
        temperatures,
        key=lambda t: abs(
            datetime.fromisoformat(t["recorded_at"]) - target_time
        )
    )

    logger.debug(f"Found temperature {closest['temperature']}°C for {target_time.isoformat()}")
    return closest["temperature"]


def _load_forecast_data() -> dict[datetime, float]:
    """
    Load forecast data from latest.csv.

    Returns:
        dict[datetime, float]: Dictionary mapping datetime (hour) to predicted water temperature
    """
    forecast_data = {}

    try:
        if not os.path.exists(FORECAST_CSV_PATH):
            logger.warning(f"Forecast CSV file not found at {FORECAST_CSV_PATH}")
            return forecast_data

        with open(FORECAST_CSV_PATH, 'r') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    # Parse datetime from time_hour column
                    time_hour = datetime.strptime(row['time_hour'], '%Y-%m-%d %H:%M:%S')
                    # Get predicted water temperature
                    predicted_temp = float(row['predicted_water_temp'])
                    forecast_data[time_hour] = predicted_temp
                except (ValueError, KeyError) as e:
                    logger.debug(f"Error parsing forecast row: {e}")
                    continue

        logger.info(f"Loaded {len(forecast_data)} forecast data points from CSV")

    except Exception as e:
        logger.error(f"Error loading forecast data: {e}")

    return forecast_data


def _get_predicted_temperature(target_time: datetime) -> float | None:
    """
    Get predicted water temperature for a specific time from the forecast.
    If the performance doesn't start on the hour, uses the next hour's forecast.

    Args:
        target_time: The time to get predicted temperature for

    Returns:
        float | None: Predicted temperature if found, None otherwise
    """
    try:
        forecast_data = _load_forecast_data()

        if not forecast_data:
            return None

        # Round up to the next hour if not on the hour
        if target_time.minute != 0 or target_time.second != 0:
            # Start of next hour
            next_hour = (target_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        else:
            next_hour = target_time.replace(second=0, microsecond=0)

        # Look up the forecast for this hour
        if next_hour in forecast_data:
            temp = forecast_data[next_hour]
            logger.debug(f"Found predicted temperature {temp}°C for {next_hour.isoformat()}")
            return temp
        else:
            logger.debug(f"No forecast data found for {next_hour.isoformat()}")
            return None

    except Exception as e:
        logger.error(f"Error getting predicted temperature: {e}")
        return None


def _get_current_temperature() -> float | None:
    """
    Get the current water temperature, either from cache or by fetching.

    Returns:
        float | None: Current temperature if available, None otherwise
    """
    from src.core.weather import get_cached_weather, fetch_and_cache_weather

    # Try to get from cache first
    cached_result = get_cached_weather()
    if cached_result is not None:
        weather_data, _ = cached_result
        return weather_data["water_temp"]

    # Fetch fresh data
    try:
        weather_data, _ = fetch_and_cache_weather()
        return weather_data["water_temp"]
    except Exception as e:
        logger.error(f"Failed to fetch current water temperature: {e}")
        return None


def add_temperature_to_performances(data: dict) -> dict:
    """
    Add water temperature to performances based on their timing.

    Rules:
    - Past performances: Get temperature from database for that time
    - Current performances (happening now): Get current temperature via get_water_temperature
    - Future performances: No temperature added

    Args:
        data: Calendar response data with days and performances

    Returns:
        dict: Modified data with temperature field added to applicable performances
    """
    try:
        now = datetime.now()
        days = data.get("days", [])

        for day in days:
            performances = day.get("performances", [])

            for performance in performances:
                try:
                    status = _get_performance_status(performance, now)
                    date_str = performance.get("date")
                    time_str = performance.get("time")

                    if status == "future":
                        # Add predicted temperature for future performances (next 7 days)
                        if date_str and time_str:
                            try:
                                perf_time = _parse_performance_datetime(date_str, time_str)
                                # Check if within next 7 days
                                if perf_time <= now + timedelta(days=7):
                                    predicted_temp = _get_predicted_temperature(perf_time)
                                    if predicted_temp is not None:
                                        performance["predicted_water_temp"] = predicted_temp
                                        logger.info(
                                            f"Added predicted temperature {predicted_temp}°C to performance "
                                            f"{performance.get('performanceAK', 'unknown')}"
                                        )
                            except (ValueError, KeyError) as e:
                                logger.error(f"Error getting predicted temperature for future performance: {e}")
                        continue

                    elif status == "current":
                        # Get current temperature for ongoing performances
                        temp = _get_current_temperature()
                        if temp is not None:
                            performance["water_temperature"] = temp
                            logger.info(
                                f"Added current temperature {temp}°C to performance "
                                f"{performance.get('performanceAK', 'unknown')}"
                            )

                    elif status == "past":
                        # Get historical temperature for past performances
                        if date_str and time_str:
                            try:
                                perf_time = _parse_performance_datetime(date_str, time_str)
                                temp = _get_temperature_for_time(perf_time)
                                if temp is not None:
                                    performance["water_temperature"] = temp
                                    logger.info(
                                        f"Added historical temperature {temp}°C to performance "
                                        f"{performance.get('performanceAK', 'unknown')}"
                                    )
                            except (ValueError, KeyError) as e:
                                logger.error(f"Error getting temperature for past performance: {e}")

                except Exception as e:
                    # Log error but continue processing other performances
                    logger.error(
                        f"Error processing temperature for performance "
                        f"{performance.get('performanceAK', 'unknown')}: {e}"
                    )
                    continue

    except Exception as e:
        # If there's a critical error, log it but return data unchanged
        logger.error(f"Critical error in add_temperature_to_performances: {e}", exc_info=True)

    return data
