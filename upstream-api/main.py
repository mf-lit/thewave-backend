import os
import time
import logging
import re
import json
import threading
from pathlib import Path
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

# Test mode configuration (enabled when TEST_MODE env var is set)
TEST_MODE = os.getenv("TEST_MODE", "").lower() in ("true", "1", "yes")

# In-memory cache: {cache_key: {"data": dict, "timestamp": float, "expires": float}}
_cache: dict[str, dict] = {}
# Water temperature cache: {cache_key: {"data": float, "timestamp": float, "expires": float}}
_water_temp_cache: dict[str, dict] = {}
# Lock to prevent concurrent water temperature scraping
_water_temp_lock = threading.Lock()

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


def _load_test_data() -> dict:
    """
    Load test data from response.json file.
    This function reads the file fresh on each call (no caching) to allow
    live editing of response.json during development.
    
    Returns:
        dict: Parsed JSON data from response.json
    
    Raises:
        FileNotFoundError: If response.json is not found
        json.JSONDecodeError: If response.json contains invalid JSON
    """
    # Get the directory where main.py is located
    script_dir = Path(__file__).parent
    response_file = script_dir / "response.json"
    
    if not response_file.exists():
        raise FileNotFoundError(f"Test data file not found: {response_file}")
    
    # Read file fresh on each call (no caching) - file is opened and closed each time
    logger.info(f"Reading test data from {response_file} (fresh read)")
    with open(response_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    return data


def _transform_dates_in_response(data: dict, date_from: str, number_of_days: str) -> dict:
    """
    Transform dates in the response data to match the requested date range.
    
    Args:
        data: Response data dictionary from test file
        date_from: Start date in YYYY-MM-DD format
        number_of_days: Number of days to fetch (as string)
    
    Returns:
        dict: Response data with dates transformed
    """
    # Parse the date_from string
    base_date = datetime.strptime(date_from, "%Y-%m-%d")
    num_days = int(number_of_days)
    
    # Deep copy the data to avoid modifying the original
    import copy
    transformed_data = copy.deepcopy(data)
    
    # Get the template day from the test data (first day)
    if not isinstance(transformed_data, dict) or "days" not in transformed_data:
        return transformed_data
    
    days_list = transformed_data.get("days", [])
    if not isinstance(days_list, list) or len(days_list) == 0:
        return transformed_data
    
    template_day = days_list[0]
    
    # Generate days for the requested date range
    new_days = []
    for day_offset in range(num_days):
        current_date = base_date + timedelta(days=day_offset)
        current_date_str = current_date.strftime("%Y-%m-%d")
        
        # Deep copy the template day
        new_day = copy.deepcopy(template_day)
        
        # Update the day's date
        new_day["date"] = current_date_str
        
        # Update all performance dates
        if isinstance(new_day, dict) and "performances" in new_day:
            performances = new_day.get("performances", [])
            if isinstance(performances, list):
                for performance in performances:
                    if isinstance(performance, dict) and "date" in performance:
                        performance["date"] = current_date_str
        
        new_days.append(new_day)
    
    # Replace the days array with the transformed days
    transformed_data["days"] = new_days
    
    return transformed_data


def _add_side_to_availability(data: dict) -> dict:
    """
    Add 'side' field to availabilityPerProduct objects based on code suffix.
    
    Args:
        data: Response data dictionary
    
    Returns:
        dict: Response data with side fields added
    """
    if not isinstance(data, dict) or "days" not in data:
        return data
    
    days = data.get("days", [])
    if not isinstance(days, list):
        return data
    
    for day in days:
        if not isinstance(day, dict) or "performances" not in day:
            continue
        
        performances = day.get("performances", [])
        if not isinstance(performances, list):
            continue
        
        for performance in performances:
            if not isinstance(performance, dict) or "availabilityPerProduct" not in performance:
                continue
            
            availability_per_product = performance.get("availabilityPerProduct")
            # Handle both single object and array cases
            if isinstance(availability_per_product, list):
                for item in availability_per_product:
                    if isinstance(item, dict) and "code" in item:
                        code = item.get("code", "")
                        if code.endswith("-L"):
                            item["side"] = "left"
                        elif code.endswith("-R"):
                            item["side"] = "right"
                        else:
                            item["side"] = "none"
            elif isinstance(availability_per_product, dict) and "code" in availability_per_product:
                code = availability_per_product.get("code", "")
                if code.endswith("-L"):
                    availability_per_product["side"] = "left"
                elif code.endswith("-R"):
                    availability_per_product["side"] = "right"
                else:
                    availability_per_product["side"] = "none"
    
    return data


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
    
    # Test mode: use dummy data from response.json
    if TEST_MODE:
        logger.info(f"Test mode enabled: using dummy data for dateFrom={date_from}, numberOfDays={number_of_days}")
        try:
            # Load test data
            test_data = _load_test_data()
            # Transform dates to match requested date range
            response_data = _transform_dates_in_response(test_data, date_from, number_of_days)
            # Add side field to availabilityPerProduct
            response_data = _add_side_to_availability(response_data)
            # Set expiration time (1 hour from now for test mode)
            expires = time.time() + 3600
            # Add expires field - handle both dict and list responses
            if isinstance(response_data, dict):
                response_with_expires = {**response_data, "expires": int(expires)}
            else:
                # For non-dict responses (e.g., arrays), wrap in an object
                response_with_expires = {"data": response_data, "expires": int(expires)}
            return jsonify(response_with_expires)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load test data: {str(e)}")
            return jsonify({"error": f"Test mode error: {str(e)}"}), 500
    
    # Generate cache key
    cache_key = _get_cache_key(date_from, number_of_days)
    
    # Check cache unless refresh is requested
    if not refresh:
        cached_result = _get_from_cache(cache_key)
        if cached_result is not None:
            cached_data, expires = cached_result
            # Add side field to availabilityPerProduct
            cached_data = _add_side_to_availability(cached_data)
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
        # Store original data in cache (without side field)
        _store_in_cache(cache_key, response_data)
        # Add side field to availabilityPerProduct
        response_data = _add_side_to_availability(response_data)
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
    # Check cache first
    cached_result = _get_water_temp_from_cache()
    if cached_result is not None:
        temperature, expires = cached_result
        return jsonify(temperature)
    
    # Use lock to prevent concurrent scraping (race condition fix)
    with _water_temp_lock:
        # Double-check cache after acquiring lock (another request may have populated it)
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
