import logging
import os
import csv
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Literal

logger = logging.getLogger(__name__)

# Path to forecast CSV file
FORECAST_CSV_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data/latest.csv"
)

# Forecast cache: stores loaded data and the source file's mtime at load time.
# Reload is triggered when the on-disk mtime changes, so the cache tracks
# whatever cadence the producer rewrites latest.csv at.
_forecast_cache: dict = {
    "data": {},           # dict[datetime, float] - the forecast data
    "loaded_mtime": None, # float - st_mtime of latest.csv at load time
}

# LRU cache size for historical temperature lookups
HISTORICAL_TEMP_CACHE_SIZE = int(os.getenv("HISTORICAL_TEMP_CACHE_SIZE", "256"))


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


@lru_cache(maxsize=HISTORICAL_TEMP_CACHE_SIZE)
def _get_temperature_for_time_cached(target_time_iso: str) -> float | None:
    """
    Get the water temperature for a specific time (cached version).
    Looks for the closest temperature reading in the database.

    Args:
        target_time_iso: ISO format string of the target time (for cache key hashability)

    Returns:
        float | None: Temperature if found, None otherwise
    """
    from src.core.water_temp_db import get_temperature_history

    target_time = datetime.fromisoformat(target_time_iso)

    # Get temperatures within +/- 2 hours of target time
    start_time = target_time - timedelta(hours=2)
    end_time = target_time + timedelta(hours=2)

    temperatures = get_temperature_history(
        limit=10,
        start_date=start_time.isoformat(),
        end_date=end_time.isoformat()
    )

    if not temperatures:
        logger.debug(f"No temperature data found near {target_time_iso}")
        return None

    # Find the closest temperature reading
    closest = min(
        temperatures,
        key=lambda t: abs(
            datetime.fromisoformat(t["recorded_at"]) - target_time
        )
    )

    logger.debug(f"Found temperature {closest['temperature']}°C for {target_time_iso}")
    return closest["temperature"]


def _get_temperature_for_time(target_time: datetime) -> float | None:
    """
    Get the water temperature for a specific time.
    Results are cached using LRU cache since historical data doesn't change.

    Args:
        target_time: The time to get temperature for

    Returns:
        float | None: Temperature if found, None otherwise
    """
    return _get_temperature_for_time_cached(target_time.isoformat())


def _current_forecast_mtime() -> float | None:
    """Return st_mtime of the forecast CSV, or None if it's missing/unreadable."""
    try:
        return os.path.getmtime(FORECAST_CSV_PATH)
    except OSError:
        return None


def _should_reload_forecast() -> bool:
    """
    Reload when the on-disk forecast file's mtime differs from what we
    loaded last. First call (loaded_mtime is None) always reloads if the
    file exists.
    """
    disk_mtime = _current_forecast_mtime()
    if disk_mtime is None:
        # File missing — nothing to load; keep whatever's cached.
        return False
    return disk_mtime != _forecast_cache["loaded_mtime"]


def _load_forecast_data_from_file() -> dict[datetime, float]:
    """
    Load forecast data directly from latest.csv file.

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


def _load_forecast_data() -> dict[datetime, float]:
    """
    Get forecast data, reloading from disk whenever latest.csv's mtime changes.

    Returns:
        dict[datetime, float]: Dictionary mapping datetime (hour) to predicted water temperature
    """
    if _should_reload_forecast():
        # Capture mtime before reading; if producer atomically renames the
        # file (the common case), this stamp matches the data we're about
        # to read. If the file is rewritten in place mid-read, the next
        # mtime will differ and we'll reload on the next call.
        new_mtime = _current_forecast_mtime()
        logger.info("Reloading forecast data (latest.csv mtime changed)")
        _forecast_cache["data"] = _load_forecast_data_from_file()
        _forecast_cache["loaded_mtime"] = new_mtime

    return _forecast_cache["data"]


def _get_predicted_temperature(target_time: datetime) -> tuple[float | None, bool]:
    """
    Get predicted water temperature for a specific time from the forecast.
    If the performance doesn't start on the hour, uses the next hour's forecast.

    Args:
        target_time: The time to get predicted temperature for

    Returns:
        tuple[float | None, bool]: (temperature, is_estimate).
            - temperature is None only when the forecast holds no data at all.
            - is_estimate is True when the exact forecast hour was unavailable
              and the nearest / carried-forward hour was substituted instead
              (e.g. the forecast pipeline is stale because of a stuck sensor and
              the slot sits beyond the last forecast hour, or the hourly grid
              skipped this exact hour).
    """
    try:
        forecast_data = _load_forecast_data()

        if not forecast_data:
            return None, False

        # Round up to the next hour if not on the hour
        if target_time.minute != 0 or target_time.second != 0:
            # Start of next hour
            next_hour = (target_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        else:
            next_hour = target_time.replace(second=0, microsecond=0)

        # Exact hit: a genuine in-horizon forecast for this hour.
        if next_hour in forecast_data:
            temp = round(forecast_data[next_hour], 1)
            logger.debug(f"Found predicted temperature {temp}°C for {next_hour.isoformat()}")
            return temp, False

        # No exact hour available. Rather than return nothing, substitute the
        # closest forecast hour we do have. This covers internal gaps, an
        # on-the-hour slot the grid happens to skip, and — most importantly —
        # slots beyond the last forecast hour when the pipeline is stale, where
        # the closest hour is the final (carried-forward) forecast value.
        closest_hour = min(forecast_data.keys(), key=lambda h: abs(h - next_hour))
        temp = round(forecast_data[closest_hour], 1)
        logger.debug(
            f"No exact forecast for {next_hour.isoformat()}; substituting nearest "
            f"{closest_hour.isoformat()} ({temp}°C, marked as estimate)"
        )
        return temp, True

    except Exception as e:
        logger.error(f"Error getting predicted temperature: {e}")
        return None, False


def _get_latest_known_temperature() -> float | None:
    """
    Last-resort fallback: the most recent actual reading from the water-temp DB.

    Used to guarantee every performance carries a temperature even when neither
    the normal forecast nor a near-in-time historical reading is available.

    Returns:
        float | None: Most recent recorded temperature, or None if the DB is empty.
    """
    from src.core.water_temp_db import get_latest_temperature

    try:
        latest = get_latest_temperature()
        if latest is not None and latest.get("temperature") is not None:
            return round(float(latest["temperature"]), 1)
    except Exception as e:
        logger.error(f"Error getting latest known temperature: {e}")
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
    - Future performances: Get predicted temperature from the forecast

    Safety net: whatever the status, if a performance would otherwise carry
    neither ``waterTemperature`` nor ``predictedWaterTemp`` (stuck sensor, stale
    forecast pipeline, gap in the historical DB, ...), it is backfilled with the
    most recent known reading and flagged ``waterTempIsEstimate: true`` so at
    least one temperature is always served. ``waterTempIsEstimate`` is also set
    when a future slot's forecast hour had to be substituted by a nearby /
    carried-forward hour. The flag is only present when True.

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
                                    predicted_temp, is_estimate = _get_predicted_temperature(perf_time)
                                    if predicted_temp is not None:
                                        performance["predictedWaterTemp"] = predicted_temp
                                        if is_estimate:
                                            performance["waterTempIsEstimate"] = True
                                        logger.debug(
                                            f"Added predicted temperature {predicted_temp}°C to performance "
                                            f"{performance.get('performanceAK', 'unknown')}"
                                            f"{' (estimate)' if is_estimate else ''}"
                                        )
                            except (ValueError, KeyError) as e:
                                logger.error(f"Error getting predicted temperature for future performance: {e}")

                    elif status == "current":
                        # Get current temperature for ongoing performances
                        temp = _get_current_temperature()
                        if temp is not None:
                            performance["waterTemperature"] = temp
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
                                    performance["waterTemperature"] = temp
                                    logger.info(
                                        f"Added historical temperature {temp}°C to performance "
                                        f"{performance.get('performanceAK', 'unknown')}"
                                    )
                            except (ValueError, KeyError) as e:
                                logger.error(f"Error getting temperature for past performance: {e}")

                    # Safety net: guarantee every performance carries at least
                    # one temperature. Fires when the steps above produced
                    # nothing — a stuck sensor freezing the current reading, a
                    # stale forecast with no nearby hour, a gap in the historical
                    # DB. Backfill from the most recent known reading and flag it.
                    #
                    # Scope must match the forecast window: future slots more
                    # than 7 days out are intentionally left bare (we have no
                    # forecast nor any reading for them — a "prediction" that far
                    # ahead would just be today's value, which is meaningless).
                    eligible_for_fallback = True
                    if status == "future":
                        if date_str and time_str:
                            try:
                                perf_time = _parse_performance_datetime(date_str, time_str)
                                eligible_for_fallback = perf_time <= now + timedelta(days=7)
                            except (ValueError, KeyError):
                                eligible_for_fallback = False
                        else:
                            eligible_for_fallback = False

                    if (
                        eligible_for_fallback
                        and performance.get("waterTemperature") is None
                        and performance.get("predictedWaterTemp") is None
                    ):
                        fallback = _get_latest_known_temperature()
                        if fallback is not None:
                            if status == "future":
                                performance["predictedWaterTemp"] = fallback
                            else:
                                performance["waterTemperature"] = fallback
                            performance["waterTempIsEstimate"] = True
                            logger.info(
                                f"Applied fallback temperature {fallback}°C to performance "
                                f"{performance.get('performanceAK', 'unknown')} (status={status})"
                            )

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
