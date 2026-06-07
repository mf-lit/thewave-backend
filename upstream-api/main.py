import os
import time
import logging
import json
import sys
from datetime import datetime, timedelta
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
from src.core.performance_price import add_prices_to_performances
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

# In-memory cache keyed by individual day: {"YYYY-MM-DD": {"day": dict, "timestamp": float, "expires": float}}
# Caching by day (not by request window) lets any cached date serve any window that includes it,
# so overlapping requests never re-fetch the same day from upstream.
_day_cache: dict[str, dict] = {}

# Cache TTL in seconds (default: 600, configurable via CACHE_TTL_SECONDS env var)
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "600"))

# Allow force-refresh via query parameter (default: true, set ALLOW_FORCE_REFRESH=false to disable)
ALLOW_FORCE_REFRESH = os.getenv("ALLOW_FORCE_REFRESH", "true").lower() not in ("false", "0", "no")

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes and origins


class CloudflareRemoteAddr:
    """WSGI middleware to surface the true client IP.

    The app sits behind a Cloudflare tunnel, so the WSGI REMOTE_ADDR is the
    tunnel's address (e.g. 172.28.0.x). Cloudflare passes the real client IP in
    the CF-Connecting-IP header. Rewriting REMOTE_ADDR here makes both gunicorn's
    access log (%(h)s) and Flask's request.remote_addr report the real client.
    """

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        cf_ip = environ.get("HTTP_CF_CONNECTING_IP")
        if cf_ip:
            environ["REMOTE_ADDR"] = cf_ip
        return self.wsgi_app(environ, start_response)


app.wsgi_app = CloudflareRemoteAddr(app.wsgi_app)

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
            track_client(client_id, client_os=client_os, client_version=client_version, client_ip=request.remote_addr)
        except Exception as e:
            logger.error(f"Failed to track client {client_id}: {e}")



def _get_cached_day(date: str) -> tuple[dict, float] | None:
    """Return (day_data, expires) if the date is cached and unexpired, else None.

    Evicts the entry on TTL expiry as a side effect.
    """
    entry = _day_cache.get(date)
    if entry is None:
        return None
    if time.time() - entry["timestamp"] >= CACHE_TTL_SECONDS:
        del _day_cache[date]
        return None
    return (entry["day"], entry["expires"])


def _store_days_in_cache(days: list) -> None:
    """Store each day from a fresh upstream response under its `date` field."""
    timestamp = time.time()
    expires = timestamp + CACHE_TTL_SECONDS
    for day in days:
        if not isinstance(day, dict):
            continue
        date = day.get("date")
        if not date:
            continue
        _day_cache[date] = {"day": day, "timestamp": timestamp, "expires": expires}


def _split_into_runs(dates: list) -> list[tuple]:
    """Group ascending dates into contiguous (start_date, length) runs."""
    if not dates:
        return []
    runs = []
    run_start = dates[0]
    run_len = 1
    prev = dates[0]
    for d in dates[1:]:
        if (d - prev).days == 1:
            run_len += 1
        else:
            runs.append((run_start, run_len))
            run_start = d
            run_len = 1
        prev = d
    runs.append((run_start, run_len))
    return runs


def _empty_day(date_str: str) -> dict:
    return {
        "availability": None,
        "date": date_str,
        "hasNoTimeSlot": None,
        "performances": [],
        "priceMax": None,
        "priceMin": None,
    }


def _get_current_days(date_from: str, number_of_days: int, refresh: bool) -> tuple[list, float]:
    """Return (days, min_expires) for a contiguous current/future window.

    Looks up each requested date in the per-day cache and only fetches contiguous runs
    of missing dates from upstream. Each fetched day is stored individually so future
    overlapping requests reuse it. Raises requests.RequestException on upstream failure.
    """
    base_date = datetime.strptime(date_from, "%Y-%m-%d").date()
    requested_dates = [base_date + timedelta(days=i) for i in range(number_of_days)]

    if refresh:
        missing_dates = list(requested_dates)
        logger.info(
            f"Force-refresh requested for dateFrom={date_from}, numberOfDays={number_of_days}, fetching from upstream"
        )
    else:
        missing_dates = [
            d for d in requested_dates
            if _get_cached_day(d.strftime("%Y-%m-%d")) is None
        ]
        if missing_dates:
            missing_strs = [d.strftime("%Y-%m-%d") for d in missing_dates]
            logger.info(
                f"Cache miss for {missing_strs} (request: dateFrom={date_from}, numberOfDays={number_of_days}), fetching from upstream"
            )

    for run_start, run_len in _split_into_runs(missing_dates):
        run_start_str = run_start.strftime("%Y-%m-%d")
        run_len_str = str(run_len)
        response_data = get_calendar(run_start_str, run_len_str)
        response_data = normalize_calendar_response(response_data, run_start_str, run_len)
        response_data = add_side_to_availability(response_data)
        _store_days_in_cache(response_data.get("days", []))

    assembled_days = []
    min_expires = float("inf")
    for d in requested_dates:
        d_str = d.strftime("%Y-%m-%d")
        result = _get_cached_day(d_str)
        if result is None:
            assembled_days.append(_empty_day(d_str))
        else:
            day_data, expires = result
            assembled_days.append(day_data)
            if expires < min_expires:
                min_expires = expires

    if min_expires == float("inf"):
        min_expires = time.time() + CACHE_TTL_SECONDS

    return assembled_days, min_expires


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


def _fetch_current_days(date_from, number_of_days_str, number_of_days, refresh):
    """Serve a current/future window using per-day cache + run-batched upstream fetches."""
    try:
        days, expires = _get_current_days(date_from, number_of_days, refresh)
    except requests.exceptions.RequestException as e:
        logger.error(f"Upstream API call failed: dateFrom={date_from}, numberOfDays={number_of_days}, error={str(e)}")
        return jsonify({"error": f"Upstream API error: {str(e)}"}), 500

    response_data = {"days": days}
    response_data = add_temperature_to_performances(response_data)
    response_data = add_floodlights_to_performances(response_data)
    response_data = add_prices_to_performances(response_data)
    return jsonify(_format_response_with_expires(response_data, expires))


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
            response_data = add_prices_to_performances(response_data)
            expires = time.time() + 3600
            return jsonify(_format_response_with_expires(response_data, expires))
        except (FileNotFoundError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load test data: {str(e)}")
            return jsonify({"error": f"Test mode error: {str(e)}"}), 500
    
    # Determine which days are past vs present/future
    try:
        requested_date = datetime.strptime(date_from, "%Y-%m-%d").date()
        today = datetime.now().date()
    except ValueError as e:
        logger.error(f"Invalid date format: {date_from}, error: {str(e)}")
        return jsonify({"error": f"Invalid date format: {date_from}"}), 400

    end_date = requested_date + timedelta(days=number_of_days - 1)

    # All days are in the past — serve entirely from history
    if end_date < today:
        logger.info(f"Requesting historical data for dateFrom={date_from}, numberOfDays={number_of_days}")
        response_data = build_multi_day_response(date_from, number_of_days)
        expires = time.time() + 3600
        return jsonify(_format_response_with_expires(response_data, expires))

    # All days are present/future — serve entirely from upstream
    if requested_date >= today:
        return _fetch_current_days(date_from, number_of_days_str, number_of_days, refresh)

    # Mixed range: some past days + some present/future days
    past_days_count = (today - requested_date).days
    future_days_count = number_of_days - past_days_count
    future_date_from = today.strftime("%Y-%m-%d")

    logger.info(
        f"Mixed date range: {past_days_count} past day(s) from {date_from}, "
        f"{future_days_count} current/future day(s) from {future_date_from}"
    )

    # Fetch past days from history
    history_data = build_multi_day_response(date_from, past_days_count)
    history_days = history_data.get("days", [])

    # Fetch current/future days via the per-day cache (refresh=False; refresh isn't supported
    # for mixed-range requests since the past portion comes from immutable history files)
    future_expires = time.time() + CACHE_TTL_SECONDS
    try:
        upstream_days, future_expires = _get_current_days(future_date_from, future_days_count, refresh=False)
        upstream_payload = add_temperature_to_performances({"days": upstream_days})
        upstream_payload = add_floodlights_to_performances(upstream_payload)
        upstream_payload = add_prices_to_performances(upstream_payload)
        upstream_days = upstream_payload.get("days", [])
    except requests.exceptions.RequestException as e:
        logger.error(f"Upstream API call failed for future portion: {str(e)}")
        upstream_days = []

    # Combine
    combined = {"days": history_days + upstream_days}
    if "_warnings" in history_data:
        combined["_warnings"] = history_data["_warnings"]
    return jsonify(_format_response_with_expires(combined, future_expires))


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