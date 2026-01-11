import os
import time
import logging
import re
import json
import threading
import sys
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import bs4 as bs

# Import our modules
from src.core.wave_calendar import (
    get_calendar,
    load_test_data,
    transform_dates_in_response,
    add_side_to_availability
)

from src.core.history import build_multi_day_response, normalize_calendar_response
from src.core.auth import load_api_keys, require_api_key
# Don't import scheduler at module level to avoid circular imports

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Test mode configuration (enabled when TEST_MODE env var is set)
TEST_MODE = os.getenv("TEST_MODE", "").lower() in ("true", "1", "yes")

# In-memory cache: {cache_key: {"data": dict, "timestamp": float, "expires": float}}
_cache: dict[str, dict] = {}
# Weather cache: {cache_key: {"data": dict, "timestamp": float, "expires": float}}
# Data format: {"water_temp": float, "air_temp": float, "conditions": str}
_weather_cache: dict[str, dict] = {}
# Lock to prevent concurrent weather scraping
_weather_lock = threading.Lock()

# Cache TTL in seconds (default: 600, configurable via CACHE_TTL_SECONDS env var)
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "600"))

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes and origins

# Load API keys at startup
try:
    load_api_keys()
except ValueError as e:
    logger.error(f"Failed to start: {str(e)}")
    print(f"\nERROR: {str(e)}\n", file=sys.stderr)
    sys.exit(1)

# Register authentication check for all requests
@app.before_request
def check_authentication():
    """Check x-api-key header on all requests."""
    return require_api_key()



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
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
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
        Weather data format: {"water_temp": float, "air_temp": float, "conditions": str}
    """
    cache_key = "weather"
    cache_entry = _weather_cache.get(cache_key)
    if cache_entry and _is_weather_cache_valid(cache_entry):
        return (cache_entry["data"], cache_entry["expires"])
    # Remove expired entry
    if cache_key in _weather_cache:
        del _weather_cache[cache_key]
    return None


def _store_weather_in_cache(water_temp: float, air_temp: float, conditions: str) -> None:
    """
    Store weather data in cache with expiration at the start of the next hour.
    
    Args:
        water_temp: Water temperature to store
        air_temp: Air temperature to store
        conditions: Weather conditions string to store
    """
    cache_key = "weather"
    timestamp = time.time()
    expires = _get_next_hour_timestamp()
    _weather_cache[cache_key] = {
        "data": {
            "water_temp": water_temp,
            "air_temp": air_temp,
            "conditions": conditions
        },
        "timestamp": timestamp,
        "expires": expires
    }


def _get_cache_key(date_from: str, number_of_days: str) -> str:
    """
    Generate a cache key from date_from and number_of_days.
    
    Args:
        date_from: Start date in YYYY-MM-DD format
        number_of_days: Number of days to fetch (as string)
    
    Returns:
        str: Cache key
    """
    return f"{date_from}:{number_of_days}"


def _is_cache_valid(cache_entry: dict, ttl: int) -> bool:
    """
    Check if a cache entry is still valid based on TTL.
    
    Args:
        cache_entry: Cache entry with "data" and "timestamp" keys
        ttl: Time to live in seconds
    
    Returns:
        bool: True if cache entry is still valid, False otherwise
    """
    if not cache_entry:
        return False
    current_time = time.time()
    age = current_time - cache_entry["timestamp"]
    return age < ttl


def _get_from_cache(key: str) -> tuple[dict, float] | None:
    """
    Retrieve data from cache if it exists and is still valid.
    
    Args:
        key: Cache key
    
    Returns:
        tuple[dict, float] | None: (Cached data, expiration time) if valid, None otherwise
    """
    cache_entry = _cache.get(key)
    if cache_entry and _is_cache_valid(cache_entry, CACHE_TTL_SECONDS):
        return (cache_entry["data"], cache_entry["expires"])
    # Remove expired entry
    if key in _cache:
        del _cache[key]
    return None


def _store_in_cache(key: str, data: dict) -> None:
    """
    Store data in cache with current timestamp and expiration time.

    Args:
        key: Cache key
        data: Data to store
    """
    timestamp = time.time()
    expires = timestamp + CACHE_TTL_SECONDS
    _cache[key] = {
        "data": data,
        "timestamp": timestamp,
        "expires": expires
    }


def _format_response_with_expires(data: dict, expires: float) -> dict:
    """
    Format response data with expires field.

    Args:
        data: Response data (dict or other)
        expires: Expiration timestamp

    Returns:
        dict: Response with expires field added
    """
    if isinstance(data, dict):
        return {**data, "expires": int(expires)}
    return {"data": data, "expires": int(expires)}


@app.route("/calendar", methods=["GET"])
def calendar_endpoint():
    """
    GET /calendar endpoint that caches upstream API responses.
    For past dates, serves historical data from saved files.
    
    Query Parameters:
        dateFrom (required): Start date in YYYY-MM-DD format
        numberOfDays (optional, default: "1"): Number of days to fetch
        refresh (optional): If present/true, bypass cache and fetch from upstream
    
    Returns:
        JSON response identical to upstream API format
    """
    # Validate required parameter
    date_from = request.args.get("dateFrom")
    if not date_from:
        return jsonify({"error": "Missing required parameter: dateFrom"}), 400
    
    # Get optional parameters
    number_of_days_str = request.args.get("numberOfDays", "1")
    number_of_days = int(number_of_days_str)
    refresh = request.args.get("refresh", "").lower() in ("true", "1", "yes")
    
    # Test mode: use dummy data from response.json for all dates (past, present, future)
    if TEST_MODE:
        logger.info(f"Test mode enabled: using dummy data for dateFrom={date_from}, numberOfDays={number_of_days}")
        try:
            test_data = load_test_data()
            response_data = transform_dates_in_response(test_data, date_from, number_of_days_str)
            response_data = add_side_to_availability(response_data)
            expires = time.time() + 3600
            return jsonify(_format_response_with_expires(response_data, expires))
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load test data: {str(e)}")
            return jsonify({"error": f"Test mode error: {str(e)}"}), 500
    
    # Check if requesting past dates (history feature - only in production mode)
    try:
        requested_date = datetime.strptime(date_from, "%Y-%m-%d").date()
        today = datetime.now().date()

        if requested_date < today:
            logger.info(f"Requesting historical data for dateFrom={date_from}, numberOfDays={number_of_days}")
            response_data = build_multi_day_response(date_from, number_of_days)
            response_data = add_side_to_availability(response_data)
            expires = time.time() + 3600
            return jsonify(_format_response_with_expires(response_data, expires))
    except ValueError as e:
        logger.error(f"Invalid date format: {date_from}, error: {str(e)}")
        return jsonify({"error": f"Invalid date format: {date_from}"}), 400
    
    # For current/future dates: use upstream API with caching
    cache_key = _get_cache_key(date_from, number_of_days_str)

    # Check cache unless refresh is requested
    if not refresh:
        cached_result = _get_from_cache(cache_key)
        if cached_result is not None:
            cached_data, expires = cached_result
            cached_data = add_side_to_availability(cached_data)
            return jsonify(_format_response_with_expires(cached_data, expires))
    
    # Fetch from upstream API
    try:
        response_data = get_calendar(date_from, number_of_days_str)
        # Normalize response to ensure proper structure (handles "No schedule data available" cases)
        response_data = normalize_calendar_response(response_data, date_from, number_of_days)
        _store_in_cache(cache_key, response_data)
        response_data = add_side_to_availability(response_data)
        expires = time.time() + CACHE_TTL_SECONDS
        return jsonify(_format_response_with_expires(response_data, expires))
    except requests.exceptions.RequestException as e:
        logger.error(f"Upstream API call failed: dateFrom={date_from}, numberOfDays={number_of_days}, error={str(e)}")
        return jsonify({"error": f"Upstream API error: {str(e)}"}), 500


@app.route("/water-temperature", methods=["GET"])
def water_temperature_endpoint():
    """
    GET /water-temperature endpoint that returns the current water temperature.
    The temperature is cached until the start of the next hour.
    
    Returns:
        JSON response with a single float value
    """
    cached_result = _get_weather_from_cache()
    if cached_result is not None:
        weather_data, _expires = cached_result
        return jsonify(weather_data["water_temp"])

    # Use lock to prevent concurrent scraping (double-check pattern)
    with _weather_lock:
        cached_result = _get_weather_from_cache()
        if cached_result is not None:
            weather_data, _expires = cached_result
            return jsonify(weather_data["water_temp"])
        
        # Fetch from upstream
        try:
            water_temp, air_temp, conditions = get_wave_weather()
            # Store in cache
            _store_weather_in_cache(water_temp, air_temp, conditions)
            return jsonify(water_temp)
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.error(f"Water temperature scrape failed: {str(e)}")
            return jsonify({"error": f"Failed to fetch water temperature: {str(e)}"}), 500


@app.route("/wave-weather", methods=["GET"])
def wave_weather_endpoint():
    """
    GET /wave-weather endpoint that returns water temperature, air temperature, and conditions.
    The data is cached until the start of the next hour.
    
    Returns:
        JSON response with water_temp, air_temp, and conditions
    """
    cached_result = _get_weather_from_cache()
    if cached_result is not None:
        weather_data, expires = cached_result
        return jsonify(_format_response_with_expires(weather_data, expires))

    # Use lock to prevent concurrent scraping (double-check pattern)
    with _weather_lock:
        cached_result = _get_weather_from_cache()
        if cached_result is not None:
            weather_data, _expires = cached_result
            return jsonify(_format_response_with_expires(weather_data, _expires))
        
        # Fetch from upstream
        try:
            water_temp, air_temp, conditions = get_wave_weather()
            # Store in cache
            _store_weather_in_cache(water_temp, air_temp, conditions)
            weather_data = {
                "water_temp": water_temp,
                "air_temp": air_temp,
                "conditions": conditions
            }
            expires = _get_next_hour_timestamp()
            return jsonify(_format_response_with_expires(weather_data, expires))
        except (requests.exceptions.RequestException, ValueError) as e:
            logger.error(f"Weather scrape failed: {str(e)}")
            return jsonify({"error": f"Failed to fetch weather data: {str(e)}"}), 500


# Initialize scheduler for daily archive task (only when app runs, not at import time)
def _init_scheduler():
    try:
        from src.core.scheduler import setup_daily_archive_task
        setup_daily_archive_task(app)
    except ImportError as e:
        logger.warning(f"Failed to initialize scheduler: {e}")

if __name__ == "__main__":
    # Initialize scheduler before starting the app
    _init_scheduler()
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
else:
    # For production (gunicorn, etc.), initialize scheduler after app creation
    # Use Flask's before_first_request or app context
    @app.before_first_request
    def init_scheduler_on_first_request():
        _init_scheduler()