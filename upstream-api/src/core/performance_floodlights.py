import logging
from datetime import datetime

import requests

from src.core.performance_temperature import _parse_performance_datetime

logger = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# Cache sunrise/sunset times per date indefinitely: {"YYYY-MM-DD": {"sunrise": datetime, "sunset": datetime}}
_sun_times_cache: dict[str, dict] = {}


def _get_sun_times(date_str: str) -> dict | None:
    """
    Get sunrise and sunset times for a date, using cache if available.

    Args:
        date_str: Date in YYYY-MM-DD format

    Returns:
        dict with "sunrise" and "sunset" as datetime objects, or None on error
    """
    if date_str in _sun_times_cache:
        return _sun_times_cache[date_str]

    try:
        response = requests.get(OPEN_METEO_URL, params={
            "latitude": 51.54418,
            "longitude": -2.61459,
            "daily": "sunrise,sunset",
            "timezone": "Europe/London",
            "start_date": date_str,
            "end_date": date_str,
        })
        response.raise_for_status()
        data = response.json()

        sunrise = datetime.strptime(data["daily"]["sunrise"][0], "%Y-%m-%dT%H:%M")
        sunset = datetime.strptime(data["daily"]["sunset"][0], "%Y-%m-%dT%H:%M")

        result = {"sunrise": sunrise, "sunset": sunset}
        _sun_times_cache[date_str] = result
        logger.info(f"Fetched sun times for {date_str}: sunrise={sunrise.strftime('%H:%M')}, sunset={sunset.strftime('%H:%M')}")
        return result

    except Exception as e:
        logger.warning(f"Failed to get sun times for {date_str}: {e}")
        return None


def add_floodlights_to_performances(data: dict) -> dict:
    """
    Add floodlights boolean to each performance.

    A performance needs floodlights if it starts before sunrise or ends after sunset.
    Defaults to False if sun times cannot be determined.

    Args:
        data: Calendar response data with days and performances

    Returns:
        dict: Modified data with floodlights field added to each performance
    """
    for day in data.get("days", []):
        for performance in day.get("performances", []):
            performance["floodlights"] = False

            date_str = performance.get("date")
            time_str = performance.get("time")
            time_end_str = performance.get("timeEnd")

            if not all([date_str, time_str, time_end_str]):
                continue

            sun_times = _get_sun_times(date_str)
            if sun_times is None:
                continue

            try:
                start_time = _parse_performance_datetime(date_str, time_str)
                end_time = _parse_performance_datetime(date_str, time_end_str)

                if start_time < sun_times["sunrise"] or end_time > sun_times["sunset"]:
                    performance["floodlights"] = True
            except (ValueError, KeyError) as e:
                logger.error(f"Error determining floodlights for {performance.get('performanceAK', 'unknown')}: {e}")

    return data
