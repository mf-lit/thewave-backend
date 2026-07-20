import time
import logging
import re
import threading
from datetime import datetime, timedelta
import requests
import bs4 as bs

logger = logging.getLogger(__name__)

# Weather cache: {cache_key: {"data": dict, "timestamp": float, "expires": float}}
# Data format: {"water_temp": float, "air_temp": float, "conditions": str, "retrieved_at": float}
_weather_cache: dict[str, dict] = {}
# Lock to prevent concurrent weather scraping
_weather_lock = threading.Lock()


def _get_next_hour_timestamp() -> float:
    """
    Calculate the timestamp for the start of the next hour.
    
    Returns:
        float: Unix timestamp for the start of the next hour
    """
    now = datetime.now()
    # Get the start of the next hour
    next_hour = (now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    return next_hour.timestamp()


def _is_weather_cache_valid(cache_entry: dict) -> bool:
    """
    Check if a weather cache entry is still valid (until next hour).
    
    Args:
        cache_entry: Cache entry with "data", "timestamp", and "expires" keys
    
    Returns:
        bool: True if cache entry is still valid, False otherwise
    """
    if not cache_entry:
        return False
    current_time = time.time()
    return current_time < cache_entry["expires"]


def _get_weather_from_cache() -> tuple[dict, float] | None:
    """
    Retrieve weather data from cache if it exists and is still valid.
    
    Returns:
        tuple[dict, float] | None: (Cached weather data, expiration time) if valid, None otherwise
        Weather data format: {"water_temp": float, "air_temp": float, "conditions": str, "retrieved_at": float}
    """
    cache_key = "weather"
    cache_entry = _weather_cache.get(cache_key)
    if cache_entry and _is_weather_cache_valid(cache_entry):
        return (cache_entry["data"], cache_entry["expires"])
    # Remove expired entry
    if cache_key in _weather_cache:
        del _weather_cache[cache_key]
    return None


def _store_weather_in_cache(water_temp: float, air_temp: float, conditions: str, retrieved_at: float | None = None) -> None:
    """
    Store weather data in cache with expiration at the start of the next hour.
    
    Args:
        water_temp: Water temperature to store
        air_temp: Air temperature to store
        conditions: Weather conditions string to store
        retrieved_at: Timestamp when weather was retrieved. If None, uses current time.
    """
    cache_key = "weather"
    timestamp = retrieved_at if retrieved_at is not None else time.time()
    expires = _get_next_hour_timestamp()
    _weather_cache[cache_key] = {
        "data": {
            "water_temp": water_temp,
            "air_temp": air_temp,
            "conditions": conditions,
            "retrieved_at": timestamp
        },
        "timestamp": timestamp,
        "expires": expires
    }


def get_wave_weather() -> tuple[float, float, str]:
    """
    Fetch water temperature, air temperature, and weather conditions from the website by scraping.
    
    Returns:
        tuple[float, float, str]: (water_temp, air_temp, conditions)
    """
    url = "https://www.thewave.com/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
    }
    
    logger.info("Scraping weather data from website")
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            break
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
            if attempt < max_retries:
                log = logger.debug if attempt == 0 else logger.warning
                log(f"Weather scrape connection error (attempt {attempt + 1}/{max_retries + 1}), retrying in 3s: {e}")
                time.sleep(3)
            else:
                raise
    
    soup = bs.BeautifulSoup(response.content, "html5lib")
    marker = soup.find("p", string=re.compile("Water:.*"))
    
    if not marker:
        raise ValueError("Could not find water temperature marker on page")
    
    water_temp = float(re.sub("[^0-9.]", "", marker.text.strip()))
    logger.info(f"Water temperature scraped: {water_temp}")

    try:
        air_temp_element = marker.find_previous("p")
    except AttributeError:
        raise ValueError("Could not find air temperature marker on page")
    air_temp = float(re.sub("[^0-9.]", "", air_temp_element.text.strip()))
    logger.info(f"Air temperature scraped: {air_temp}")
    
    try:
        conditions_element = air_temp_element.find_previous("p")
    except AttributeError:
        raise ValueError("Could not find conditions marker on page")

    conditions = conditions_element.text.strip().rstrip(" &")
    logger.info(f"Conditions scraped: {conditions}")

    return water_temp, air_temp, conditions


def get_water_temperature() -> float:
    """
    Fetch water temperature from the website by scraping.
    Uses get_wave_weather() and extracts just the water temperature.
    
    Returns:
        float: Water temperature in degrees
    """
    water_temp, _, _ = get_wave_weather()
    return water_temp


def get_cached_weather() -> tuple[dict, float] | None:
    """
    Get weather data from cache if available and valid.
    
    Returns:
        tuple[dict, float] | None: (Cached weather data, expiration time) if valid, None otherwise
    """
    return _get_weather_from_cache()


def fetch_and_cache_weather() -> tuple[dict, float]:
    """
    Fetch weather data from upstream and cache it.
    Uses a lock to prevent concurrent scraping.
    
    Returns:
        tuple[dict, float]: (Weather data, expiration time)
    """
    # Use lock to prevent concurrent scraping (double-check pattern)
    with _weather_lock:
        # Double-check cache after acquiring lock
        cached_result = _get_weather_from_cache()
        if cached_result is not None:
            weather_data, expires = cached_result
            return (weather_data, expires)
        
        # Fetch from upstream
        water_temp, air_temp, conditions = get_wave_weather()
        retrieved_at = time.time()
        # Store in cache
        _store_weather_in_cache(water_temp, air_temp, conditions, retrieved_at)
        
        weather_data = {
            "water_temp": water_temp,
            "air_temp": air_temp,
            "conditions": conditions,
            "retrieved_at": retrieved_at,
        }
        expires = _get_next_hour_timestamp()
        return (weather_data, expires)
