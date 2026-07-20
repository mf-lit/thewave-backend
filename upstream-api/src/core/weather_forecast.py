import time
import random
import logging
import threading
from datetime import datetime, timedelta

import requests

from src.core.weather import _get_next_hour_timestamp
from src.core.performance_temperature import _parse_performance_datetime

logger = logging.getLogger(__name__)

# Open-Meteo 7-day hourly forecast for The Wave (near Bristol, UK).
# timezone=Europe/London so returned hourly times are UK-local and match the
# naive local performance datetimes directly (no conversion needed).
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_PARAMS = {
    "latitude": 51.539,
    "longitude": -2.6185,
    "hourly": "temperature_2m,precipitation_probability,wind_speed_10m,wind_gusts_10m,wind_direction_10m,weather_code",
    "wind_speed_unit": "mph",
    "timezone": "Europe/London",
}

# Forecast cache: data is dict[datetime, dict] mapping each forecast hour to a
# camelCase weather object. Refreshed at most once per hour (expires at the
# start of the next hour, like the water-temperature weather cache), plus a
# random jitter so we don't refetch at the exact top of the hour alongside
# every other client of the (free, shared) Open-Meteo API.
_forecast_cache: dict = {"data": {}, "expires": 0.0, "retry_after": 0.0}
# Lock to prevent concurrent forecast fetches.
_forecast_lock = threading.Lock()

# Spread refreshes across the first few minutes of the hour instead of all
# landing on :00, when Open-Meteo sees load from every other hour-aligned client.
FORECAST_EXPIRY_JITTER_SECONDS = 180
# After a failed fetch, wait before retrying instead of hammering Open-Meteo
# again on every subsequent request (expires isn't bumped forward on failure).
FORECAST_RETRY_COOLDOWN_SECONDS = 60


def fetch_forecast() -> dict[datetime, dict]:
    """
    Fetch the 7-day hourly forecast from Open-Meteo and parse it into a dict
    keyed by forecast hour.

    Returns:
        dict[datetime, dict]: Mapping of naive local datetime (on the hour) to a
        weather object {"temperature", "precipitationProbability", "windSpeed",
        "windGusts", "weatherCode"}.
    """
    logger.info("Fetching weather forecast from Open-Meteo")
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(FORECAST_URL, params=FORECAST_PARAMS, timeout=15)
            response.raise_for_status()
            break
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
            if attempt < max_retries:
                log = logger.debug if attempt == 0 else logger.warning
                log(f"Forecast fetch connection error (attempt {attempt + 1}/{max_retries + 1}), retrying in 3s: {e}")
                time.sleep(3)
            else:
                raise

    hourly = response.json().get("hourly", {})
    times = hourly.get("time", [])
    temperatures = hourly.get("temperature_2m", [])
    precip_probs = hourly.get("precipitation_probability", [])
    wind_speeds = hourly.get("wind_speed_10m", [])
    wind_gusts = hourly.get("wind_gusts_10m", [])
    wind_directions = hourly.get("wind_direction_10m", [])
    weather_codes = hourly.get("weather_code", [])

    forecast_data: dict[datetime, dict] = {}
    for t, temp, precip, wind, gust, wind_dir, code in zip(times, temperatures, precip_probs, wind_speeds, wind_gusts, wind_directions, weather_codes):
        if None in (t, temp, precip, wind, gust, wind_dir, code):
            continue
        try:
            dt = datetime.strptime(t, "%Y-%m-%dT%H:%M")
        except (ValueError, TypeError) as e:
            logger.debug(f"Skipping unparseable forecast time {t!r}: {e}")
            continue
        forecast_data[dt] = {
            "temperature": temp,
            "precipitationProbability": precip,
            "windSpeed": wind,
            "windGusts": gust,
            "windDirection": wind_dir,
            "weatherCode": code,
        }

    logger.info(f"Loaded {len(forecast_data)} forecast hours from Open-Meteo")
    return forecast_data


def get_forecast() -> dict[datetime, dict]:
    """
    Return the cached forecast, refreshing at most once per hour.

    On fetch failure the last cached data is returned (even if stale) so a
    transient network blip doesn't strip weather from responses; only returns
    an empty dict if nothing was ever cached. A short cooldown is applied
    after a failure so repeated requests don't retry Open-Meteo immediately.

    Returns:
        dict[datetime, dict]: Mapping of forecast hour to weather object.
    """
    now = time.time()
    if now < _forecast_cache["expires"] or now < _forecast_cache["retry_after"]:
        return _forecast_cache["data"]

    with _forecast_lock:
        # Double-check after acquiring the lock.
        now = time.time()
        if now < _forecast_cache["expires"] or now < _forecast_cache["retry_after"]:
            return _forecast_cache["data"]
        try:
            _forecast_cache["data"] = fetch_forecast()
            _forecast_cache["expires"] = _get_next_hour_timestamp() + random.uniform(0, FORECAST_EXPIRY_JITTER_SECONDS)
        except Exception as e:
            logger.error(f"Failed to fetch weather forecast, using cached data: {e}")
            _forecast_cache["retry_after"] = now + FORECAST_RETRY_COOLDOWN_SECONDS
        return _forecast_cache["data"]


def _get_weather_for_time(perf_time: datetime, forecast: dict[datetime, dict]) -> dict | None:
    """
    Look up the forecast weather for a performance start time.

    If the performance doesn't start on the hour, the next hour's forecast is
    used (e.g. 13:30 -> 14:00). Returns None when the time falls outside the
    forecast window (beyond ~7 days).
    """
    if perf_time.minute != 0 or perf_time.second != 0:
        next_hour = perf_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        next_hour = perf_time.replace(second=0, microsecond=0)

    return forecast.get(next_hour)


def add_weather_to_performances(data: dict) -> dict:
    """
    Inject a `weather` object into each performance whose start hour falls within
    the 7-day forecast window. Performances beyond the forecast horizon are left
    without a `weather` key.

    Args:
        data: Calendar response data with days and performances

    Returns:
        dict: Modified data with weather added to applicable performances
    """
    try:
        forecast = get_forecast()
        if not forecast:
            return data

        days = data.get("days", [])
        for day in days:
            performances = day.get("performances", [])

            for performance in performances:
                try:
                    date_str = performance.get("date")
                    time_str = performance.get("time")
                    if not date_str or not time_str:
                        continue

                    perf_time = _parse_performance_datetime(date_str, time_str)
                    weather = _get_weather_for_time(perf_time, forecast)
                    if weather is not None:
                        performance["weather"] = weather
                        logger.debug(
                            f"Added weather to performance "
                            f"{performance.get('performanceAK', 'unknown')}"
                        )

                except Exception as e:
                    logger.error(
                        f"Error processing weather for performance "
                        f"{performance.get('performanceAK', 'unknown')}: {e}"
                    )
                    continue

    except Exception as e:
        logger.error(f"Critical error in add_weather_to_performances: {e}", exc_info=True)

    return data
