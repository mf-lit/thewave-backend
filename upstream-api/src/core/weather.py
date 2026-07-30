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


SITE_URL = "https://www.thewave.com/"
# The site's own weather endpoint, referenced by the homepage's client-side
# state. Returns the same values the page renders, without the markup.
WEATHER_API_URL = "https://www.thewave.com/wp-json/wave/v1/weather"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
}


def _parse_temp(value: str, label: str) -> float:
    """
    Pull a temperature out of a string such as "24.1°C" or " 20 ".

    Args:
        value: Raw text containing the temperature
        label: Name of the field, used in the error message

    Returns:
        float: The parsed temperature

    Raises:
        ValueError: If no number is present in the text
    """
    match = re.search(r"-?\d+(?:\.\d+)?", value or "")
    if not match:
        raise ValueError(f"Could not parse {label} from {value!r}")
    return float(match.group())


def _get_with_retries(url: str) -> requests.Response:
    """
    GET a URL, retrying on connection-level failures.

    Args:
        url: URL to fetch

    Returns:
        requests.Response: The successful response
    """
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            response = requests.get(url, headers=_HEADERS, timeout=15)
            response.raise_for_status()
            return response
        except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
            if attempt < max_retries:
                log = logger.debug if attempt == 0 else logger.warning
                log(f"Weather fetch connection error (attempt {attempt + 1}/{max_retries + 1}), retrying in 3s: {e}")
                time.sleep(3)
            else:
                raise
    raise AssertionError("unreachable")


def _weather_from_api() -> tuple[float, float, str]:
    """
    Fetch weather from the site's JSON weather endpoint.

    Returns:
        tuple[float, float, str]: (water_temp, air_temp, conditions)

    Raises:
        ValueError: If the payload is missing the fields we need
    """
    response = _get_with_retries(WEATHER_API_URL)
    try:
        payload = response.json()
    except ValueError as e:
        raise ValueError(f"Weather endpoint did not return JSON: {e}")

    if not isinstance(payload, dict):
        raise ValueError(f"Weather endpoint returned unexpected payload type: {type(payload).__name__}")

    water_temp = _parse_temp(payload.get("waterTemp", ""), "water temperature")

    current = payload.get("current") or {}
    air_temp = _parse_temp(current.get("temp", ""), "air temperature")

    conditions = (current.get("description") or "").strip().rstrip(" &")
    if not conditions:
        raise ValueError("Weather endpoint returned no conditions description")

    return water_temp, air_temp, conditions


def _weather_from_page() -> tuple[float, float, str]:
    """
    Fetch weather by scraping the homepage. Fallback for when the JSON
    endpoint is unavailable.

    The values sit in a block of sibling <p> tags — conditions, then air
    temperature, then water temperature — where the numbers are wrapped in
    <span data-wp-text="..."> bindings. That nesting means the <p> tags have
    mixed content, so they must be matched on their full text rather than with
    BeautifulSoup's `string=` filter, which only matches single-string tags.

    Returns:
        tuple[float, float, str]: (water_temp, air_temp, conditions)

    Raises:
        ValueError: If the expected markup is not present
    """
    response = _get_with_retries(SITE_URL)
    soup = bs.BeautifulSoup(response.content, "html5lib")

    marker = soup.find(
        lambda tag: tag.name == "p" and tag.get_text().strip().startswith("Water:")
    )
    if not marker:
        raise ValueError("Could not find water temperature marker on page")
    water_temp = _parse_temp(marker.get_text(), "water temperature")

    air_temp_element = marker.find_previous("p")
    if air_temp_element is None:
        raise ValueError("Could not find air temperature marker on page")
    air_temp = _parse_temp(air_temp_element.get_text(), "air temperature")

    conditions_element = air_temp_element.find_previous("p")
    if conditions_element is None:
        raise ValueError("Could not find conditions marker on page")
    conditions = conditions_element.get_text().strip().rstrip(" &")

    return water_temp, air_temp, conditions


def get_wave_weather() -> tuple[float, float, str]:
    """
    Fetch water temperature, air temperature, and weather conditions from the site.

    Prefers the site's JSON weather endpoint and falls back to scraping the
    homepage if that endpoint fails.

    Returns:
        tuple[float, float, str]: (water_temp, air_temp, conditions)
    """
    logger.info("Fetching weather data from weather endpoint")
    try:
        water_temp, air_temp, conditions = _weather_from_api()
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.warning(f"Weather endpoint failed ({e}), falling back to page scrape")
        water_temp, air_temp, conditions = _weather_from_page()

    logger.info(f"Water temperature: {water_temp}")
    logger.info(f"Air temperature: {air_temp}")
    logger.info(f"Conditions: {conditions}")

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
