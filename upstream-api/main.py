import os
import time
import logging
import json
import sys
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

# Import our modules
from src.core.wave_calendar import (
    get_calendar,
    load_test_data,
    transform_dates_in_response,
    add_side_to_availability,
    load_upstream_api_url
)
import src.core.wave_calendar as wave_calendar

from src.core.history import build_multi_day_response, normalize_calendar_response
from src.core.performance_temperature import add_temperature_to_performances
from src.core.performance_floodlights import add_floodlights_to_performances
from src.core.auth import load_api_keys, require_api_key
from src.core.client_tracker import init_client_tracking, track_client
from src.core.weather import (
    get_cached_weather,
    fetch_and_cache_weather
)
# Don't import scheduler at module level to avoid circular imports

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Test mode configuration (enabled when TEST_MODE env var is set)
TEST_MODE = os.getenv("TEST_MODE", "").lower() in ("true", "1", "yes")

# In-memory cache for calendar data: {cache_key: {"data": dict, "timestamp": float, "expires": float}}
_cache: dict[str, dict] = {}

# Cache TTL in seconds (default: 600, configurable via CACHE_TTL_SECONDS env var)
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "600"))

# Allow force-refresh via query parameter (default: true, set ALLOW_FORCE_REFRESH=false to disable)
ALLOW_FORCE_REFRESH = os.getenv("ALLOW_FORCE_REFRESH", "true").lower() not in ("false", "0", "no")

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes and origins

# Load API keys at startup
try:
    load_api_keys()
except ValueError as e:
    logger.error(f"Failed to start: {str(e)}")
    print(f"\nERROR: {str(e)}\n", file=sys.stderr)
    sys.exit(1)

# Initialize client tracking table
init_client_tracking()

# Load upstream API URL at startup (from env var, config.yaml, or default)
load_upstream_api_url()
# Access the URL through the module to get the updated value
logger.info(f"Upstream API URL: {wave_calendar.UPSTREAM_API_URL}")

# Pre-initialize upstream API authenticated session (downloads WASM + fetches CSRF token)
if not TEST_MODE:
    from src.core.upstream_auth import get_authenticated_session
    try:
        get_authenticated_session()
        logger.info("Upstream API authenticated session initialized")
    except Exception as e:
        logger.warning(f"Failed to pre-initialize upstream auth session (will retry on first request): {e}")

# Register authentication check for all requests
@app.before_request
def check_authentication():
    """Check x-api-key header on all requests."""
    return require_api_key()

@app.before_request
def track_client_id():
    """Track client usage via optional X-Client-ID header."""
    client_id = request.headers.get("X-Client-ID")
    if client_id:
        try:
            client_os = request.headers.get("X-Client-OS")
            client_version = request.headers.get("X-Client-Version")
            track_client(client_id, client_os=client_os, client_version=client_version)
        except Exception as e:
            logger.error(f"Failed to track client {client_id}: {e}")



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
    refresh = ALLOW_FORCE_REFRESH and request.args.get("refresh", "").lower() in ("true", "1", "yes")
    
    # Test mode: use dummy data from response.json for all dates (past, present, future)
    if TEST_MODE:
        logger.info(f"Test mode enabled: using dummy data for dateFrom={date_from}, numberOfDays={number_of_days}")
        try:
            test_data = load_test_data()
            # Add side field before returning (test data is always fresh, not cached)
            response_data = transform_dates_in_response(test_data, date_from, number_of_days_str)
            response_data = add_side_to_availability(response_data)
            # Add water temperature to performances based on timing
            response_data = add_temperature_to_performances(response_data)
            response_data = add_floodlights_to_performances(response_data)
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
            # Historical data is pre-processed with side field and temperatures already added during archiving
            response_data = build_multi_day_response(date_from, number_of_days)
            # No need to add temperatures - they're already in the archived files
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
            # Cached data already has side field added
            # Add water temperature to performances (not cached, as it's time-dependent)
            cached_data = add_temperature_to_performances(cached_data)
            cached_data = add_floodlights_to_performances(cached_data)
            return jsonify(_format_response_with_expires(cached_data, expires))

    # Fetch from upstream API
    try:
        response_data = get_calendar(date_from, number_of_days_str)
        # Normalize response to ensure proper structure (handles "No schedule data available" cases)
        response_data = normalize_calendar_response(response_data, date_from, number_of_days)
        # Add side field before caching
        response_data = add_side_to_availability(response_data)
        _store_in_cache(cache_key, response_data)
        # Add water temperature to performances (after caching, as it's time-dependent)
        response_data = add_temperature_to_performances(response_data)
        response_data = add_floodlights_to_performances(response_data)
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
    cached_result = get_cached_weather()
    if cached_result is not None:
        weather_data, _expires = cached_result
        return jsonify(weather_data["water_temp"])

    # Fetch from upstream and cache
    try:
        weather_data, _expires = fetch_and_cache_weather()
        return jsonify(weather_data["water_temp"])
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
    cached_result = get_cached_weather()
    if cached_result is not None:
        weather_data, expires = cached_result
        return jsonify(_format_response_with_expires(weather_data, expires))

    # Fetch from upstream and cache
    try:
        weather_data, expires = fetch_and_cache_weather()
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
    # For production (gunicorn, etc.), initialize scheduler when app is created
    # The app object is already created at this point, so we can initialize directly
    _init_scheduler()