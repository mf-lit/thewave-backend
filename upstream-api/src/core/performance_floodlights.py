import logging
from datetime import datetime

import requests

from src.core.performance_temperature import _parse_performance_datetime

logger = logging.getLogger(__name__)

SUNRISE_SUNSET_URL = "https://api.sunrisesunset.io/json"

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
        response = requests.get(SUNRISE_SUNSET_URL, params={
            "lat": 51.54418,
            "lng": -2.61459,
            "date": date_str,
            "timezone": "Europe/London",
            "time_format": "24",
        })
        response.raise_for_status()
        data = response.json()

        if data.get("status") != "OK":
            logger.warning(f"SunriseSunset API returned status: {data.get('status')} for {date_str}")
            return None

        results = data["results"]
        sunrise = datetime.strptime(f"{date_str}T{results['sunrise']}", "%Y-%m-%dT%H:%M:%S")
        sunset = datetime.strptime(f"{date_str}T{results['sunset']}", "%Y-%m-%dT%H:%M:%S")

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
