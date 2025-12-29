import os
import time
import logging
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import bs4 as bs

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# In-memory cache: {cache_key: {"data": dict, "timestamp": float, "expires": float}}
_cache: dict[str, dict] = {}
# Water temperature cache: {cache_key: {"data": float, "timestamp": float, "expires": float}}
_water_temp_cache: dict[str, dict] = {}

# Cache TTL in seconds (default: 600, configurable via CACHE_TTL_SECONDS env var)
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "600"))

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes and origins


def get_calendar(date_from: str, number_of_days: str) -> dict:
    """
    Fetch calendar events from the upstream API.
    
    Args:
        date_from: Start date in YYYY-MM-DD format
        number_of_days: Number of days to fetch (as string)
    
    Returns:
        dict: JSON response from the API as a dictionary
    """
    url = "https://ticketing-api.thewave.com/api/twb-prod/b2c/v1/events/calendar"
    
    # Query parameters - handle array parameters as list of tuples
    # For array parameters like eventCategoryCode[], pass multiple tuples with same key
    params = [
        ("locale", "en-GB"),
        ("eventCategoryCode[]", "TWBB2C"),
        ("eventCategoryCode[]", "ALL2"),
        ("dateFrom", date_from),
        ("numberOfDays", number_of_days)
    ]
    
    # Headers
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:142.0) Gecko/20100101 Firefox/142.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "X-API-KEY": "42",
        "Origin": "https://ticketing.thewave.com",
        "Connection": "keep-alive",
        "Referer": "https://ticketing.thewave.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "DNT": "1",
        "Sec-GPC": "1"
    }
    
    # Log upstream API call
    logger.info(f"Calling upstream API: dateFrom={date_from}, numberOfDays={number_of_days}")
    
    # Make the request
    # Note: requests automatically handles compression (equivalent to --compressed)
    response = requests.get(url, params=params, headers=headers)
    
    # Log response status
    logger.info(f"Upstream API response: status_code={response.status_code}, dateFrom={date_from}, numberOfDays={number_of_days}")
    
    # Raise an error for bad status codes
    response.raise_for_status()
    
    # Return JSON response (API always returns JSON)
    return response.json()


def get_water_temperature() -> float:
    """
    Fetch water temperature from the website by scraping.
    
    Returns:
        float: Water temperature in degrees
    """
    url = "https://www.thewave.com/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36"
    }
    
    logger.info("Scraping water temperature from website")
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    soup = bs.BeautifulSoup(response.content, "html5lib")
    marker = soup.find("p", string=re.compile("Water:.*"))
    
    if not marker:
        raise ValueError("Could not find water temperature marker on page")
    
    water_temp = float(re.sub("[^0-9.]", "", marker.text.strip()))
    logger.info(f"Water temperature scraped: {water_temp}")
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


def _is_water_temp_cache_valid(cache_entry: dict) -> bool:
    """
    Check if a water temperature cache entry is still valid (until next hour).
    
    Args:
        cache_entry: Cache entry with "data", "timestamp", and "expires" keys
    
    Returns:
        bool: True if cache entry is still valid, False otherwise
    """
    if not cache_entry:
        return False
    current_time = time.time()
    return current_time < cache_entry["expires"]


def _get_water_temp_from_cache() -> tuple[float, float] | None:
    """
    Retrieve water temperature from cache if it exists and is still valid.
    
    Returns:
        tuple[float, float] | None: (Cached temperature, expiration time) if valid, None otherwise
    """
    cache_key = "water_temperature"
    cache_entry = _water_temp_cache.get(cache_key)
    if cache_entry and _is_water_temp_cache_valid(cache_entry):
        return (cache_entry["data"], cache_entry["expires"])
    # Remove expired entry
    if cache_key in _water_temp_cache:
        del _water_temp_cache[cache_key]
    return None


def _store_water_temp_in_cache(temperature: float) -> None:
    """
    Store water temperature in cache with expiration at the start of the next hour.
    
    Args:
        temperature: Water temperature to store
    """
    cache_key = "water_temperature"
    timestamp = time.time()
    expires = _get_next_hour_timestamp()
    _water_temp_cache[cache_key] = {
        "data": temperature,
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


@app.route("/calendar", methods=["GET"])
def calendar_endpoint():
    """
    GET /calendar endpoint that caches upstream API responses.
    
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
    number_of_days = request.args.get("numberOfDays", "1")
    refresh = request.args.get("refresh", "").lower() in ("true", "1", "yes")
    
    # Generate cache key
    cache_key = _get_cache_key(date_from, number_of_days)
    
    # Check cache unless refresh is requested
    if not refresh:
        cached_result = _get_from_cache(cache_key)
        if cached_result is not None:
            cached_data, expires = cached_result
            # Add expires field - handle both dict and list responses
            if isinstance(cached_data, dict):
                response_with_expires = {**cached_data, "expires": int(expires)}
            else:
                # For non-dict responses (e.g., arrays), wrap in an object
                response_with_expires = {"data": cached_data, "expires": int(expires)}
            return jsonify(response_with_expires)
    
    # Fetch from upstream API
    try:
        response_data = get_calendar(date_from, number_of_days)
        # Store in cache
        _store_in_cache(cache_key, response_data)
        # Calculate expiration time for this response
        expires = time.time() + CACHE_TTL_SECONDS
        # Add expires field - handle both dict and list responses
        if isinstance(response_data, dict):
            response_with_expires = {**response_data, "expires": int(expires)}
        else:
            # For non-dict responses (e.g., arrays), wrap in an object
            response_with_expires = {"data": response_data, "expires": int(expires)}
        return jsonify(response_with_expires)
    except requests.exceptions.RequestException as e:
        # Log error and return 500 error if upstream API call fails
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
    # Check cache
    cached_result = _get_water_temp_from_cache()
    if cached_result is not None:
        temperature, expires = cached_result
        return jsonify(temperature)
    
    # Fetch from upstream
    try:
        temperature = get_water_temperature()
        # Store in cache
        _store_water_temp_in_cache(temperature)
        return jsonify(temperature)
    except (requests.exceptions.RequestException, ValueError) as e:
        logger.error(f"Water temperature scrape failed: {str(e)}")
        return jsonify({"error": f"Failed to fetch water temperature: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
