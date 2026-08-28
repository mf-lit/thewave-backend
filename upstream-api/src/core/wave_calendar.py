import json
import logging
import copy
import os
import time
from pathlib import Path
from datetime import datetime, timedelta
import requests
import yaml

logger = logging.getLogger(__name__)

# Default upstream API URL
_DEFAULT_UPSTREAM_API_URL = "https://ticketing-api.thewave.com/api/twb-prod*base/b2c/v1/events/calendar"

# Global variable to store the upstream API URL (loaded at startup)
UPSTREAM_API_URL = _DEFAULT_UPSTREAM_API_URL


def load_upstream_api_url() -> str:
    """
    Load upstream API URL from environment variable or config.yaml file.
    
    Priority:
    1. UPSTREAM_API_URL environment variable
    2. config.yaml file (upstream_api field)
    3. Default hardcoded URL
    
    Returns:
        str: Upstream API URL
    """
    global UPSTREAM_API_URL
    
    # Check environment variable first
    env_url = os.getenv("UPSTREAM_API_URL", "").strip()
    if env_url:
        UPSTREAM_API_URL = env_url
        logger.info(f"Using upstream API URL from UPSTREAM_API_URL environment variable: {env_url}")
        return env_url
    
    # Try to load from config.yaml file
    project_root = Path(__file__).parent.parent.parent
    config_file = project_root / "config" / "config.yaml"
    
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
            
            if config and isinstance(config, dict):
                upstream_api = config.get("upstream_api")
                if upstream_api is not None:
                    # Handle both string and None cases, strip whitespace and quotes
                    upstream_api = str(upstream_api).strip().strip('"').strip("'")
                    if upstream_api:
                        UPSTREAM_API_URL = upstream_api
                        logger.info(f"Using upstream API URL from config.yaml: {upstream_api}")
                        return upstream_api
                else:
                    logger.debug(f"upstream_api field not found or is None in config.yaml")
            else:
                logger.debug(f"Config file exists but is not a valid dictionary")
        except (yaml.YAMLError, Exception) as e:
            logger.warning(f"Failed to load upstream_api from config.yaml: {str(e)}, using default")
    else:
        logger.debug(f"Config file not found at {config_file}")
    
    # Use default
    UPSTREAM_API_URL = _DEFAULT_UPSTREAM_API_URL
    logger.info(f"Using default upstream API URL: {_DEFAULT_UPSTREAM_API_URL}")
    return _DEFAULT_UPSTREAM_API_URL


def get_calendar(date_from: str, number_of_days: str) -> dict:
    """
    Fetch calendar events from the upstream API using authenticated session.

    Args:
        date_from: Start date in YYYY-MM-DD format
        number_of_days: Number of days to fetch (as string)

    Returns:
        dict: JSON response from the API as a dictionary
    """
    from src.core.upstream_auth import get_authenticated_session, reset_session, close_session_connections
    from src.core.proxy_health import wait_for_healthy_proxy

    url = UPSTREAM_API_URL
    params = {
        "locale": "en-GB",
        "dateFrom": date_from,
        "numberOfDays": number_of_days,
    }

    logger.info(f"Calling upstream API: dateFrom={date_from}, numberOfDays={number_of_days}")

    # Wait if the VPN proxy is known-unhealthy (rotation or degraded server)
    wait_for_healthy_proxy(timeout=30)

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            session = get_authenticated_session()
            response = session.get(url, params=params)

            # Re-authenticate once on 401/403 (token may have expired)
            if response.status_code in (401, 403):
                logger.warning(f"Upstream API returned {response.status_code}, re-authenticating")
                reset_session()
                session = get_authenticated_session()
                response = session.get(url, params=params)

            logger.info(f"Upstream API response: status_code={response.status_code}, dateFrom={date_from}, numberOfDays={number_of_days}")

            # Retry transient upstream 5xx errors (ticketing-api.thewave.com is flaky)
            if response.status_code >= 500 and attempt < max_retries:
                wait = 2 * (attempt + 1)  # 2s, 4s
                logger.warning(f"Upstream API returned {response.status_code} (attempt {attempt + 1}/{max_retries + 1}), retrying in {wait}s")
                time.sleep(wait)
                continue

            response.raise_for_status()
            # Correct the upstream 2h59m timeEnd bug at the boundary, so every
            # consumer (cache, history, enrichment) sees the real session end.
            return fix_buggy_performance_end_times(response.json())

        except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError) as e:
            if attempt < max_retries:
                log = logger.debug if attempt == 0 else logger.warning
                log(f"Proxy/connection error (attempt {attempt + 1}/{max_retries + 1}), retrying in 3s: {e}")
                # Discard the stale connection pool but keep auth state.
                # A 401/403 on retry would still trigger a full re-auth above.
                close_session_connections()
                time.sleep(3)
            else:
                raise


def load_test_data() -> dict:
    """
    Load test data from data/response.json file.
    This function reads the file fresh on each call (no caching) to allow
    live editing of data/response.json during development.
    
    Returns:
        dict: Parsed JSON data from data/response.json
    
    Raises:
        FileNotFoundError: If data/response.json is not found
        json.JSONDecodeError: If data/response.json contains invalid JSON
    """
    # Get project root (go up from src/core/ to project root)
    project_root = Path(__file__).parent.parent.parent
    response_file = project_root / "data" / "response.json"
    
    if not response_file.exists():
        raise FileNotFoundError(f"Test data file not found: {response_file}")
    
    # Read file fresh on each call (no caching) - file is opened and closed each time
    logger.info(f"Reading test data from {response_file} (fresh read)")
    with open(response_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    return data


def transform_dates_in_response(data: dict, date_from: str, number_of_days: str) -> dict:
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


def _get_side_from_code(code: str) -> str:
    """
    Determine side value from product code suffix.

    Args:
        code: Product code string

    Returns:
        str: "left", "right", or "none"
    """
    if code.endswith("-L"):
        return "left"
    if code.endswith("-R"):
        return "right"
    return "none"


def _add_side_to_item(item: dict) -> None:
    """
    Add side field to a single availability item in place.

    Args:
        item: Availability item dictionary with 'code' field
    """
    if isinstance(item, dict) and "code" in item:
        item["side"] = _get_side_from_code(item.get("code", ""))


def add_side_to_availability(data: dict) -> dict:
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

            availability = performance.get("availabilityPerProduct")
            if isinstance(availability, list):
                for item in availability:
                    _add_side_to_item(item)
            else:
                _add_side_to_item(availability)

    return data


# Upstream calendar bug: some performances come back with a timeEnd exactly
# 2h59m after time. Those sessions are really 59m long, like every other one.
_BUGGY_DURATION = timedelta(hours=2, minutes=59)
_CORRECTED_DURATION = timedelta(minutes=59)


def _parse_time_of_day(time_str: str) -> timedelta | None:
    """
    Parse an upstream "HH:MM:SS[.mmm]" time into an offset from midnight.

    Args:
        time_str: Time string from a performance

    Returns:
        timedelta | None: Offset from midnight, or None if unparseable
    """
    if not isinstance(time_str, str):
        return None

    try:
        parsed = datetime.strptime(time_str.split(".")[0], "%H:%M:%S")
    except ValueError:
        return None

    return timedelta(hours=parsed.hour, minutes=parsed.minute, seconds=parsed.second)


def _format_time_of_day(offset: timedelta, template: str) -> str:
    """
    Format an offset from midnight, keeping the template's fractional part.

    Args:
        offset: Offset from midnight
        template: Original time string, used to preserve the ".mmm" suffix

    Returns:
        str: Time string in the same shape as the template
    """
    total_seconds = int(offset.total_seconds())
    formatted = f"{total_seconds // 3600:02d}:{total_seconds // 60 % 60:02d}:{total_seconds % 60:02d}"

    if "." in template:
        formatted = f"{formatted}.{template.split('.', 1)[1]}"

    return formatted


def _fix_performance_time_end(performance: dict) -> None:
    """
    Override a single performance's buggy timeEnd in place.

    Args:
        performance: Performance dictionary with 'time' and 'timeEnd' fields
    """
    if not isinstance(performance, dict):
        return

    end_str = performance.get("timeEnd")
    start = _parse_time_of_day(performance.get("time"))
    end = _parse_time_of_day(end_str)

    if start is None or end is None:
        return

    duration = end - start
    if duration < timedelta(0):
        # A late session whose end wrapped past midnight
        duration += timedelta(days=1)

    if duration != _BUGGY_DURATION:
        return

    corrected = _format_time_of_day((start + _CORRECTED_DURATION) % timedelta(days=1), end_str)
    logger.info(
        f"Overriding buggy timeEnd for performance {performance.get('performanceAK', 'unknown')} "
        f"on {performance.get('date', 'unknown')}: {end_str} -> {corrected}"
    )
    performance["timeEnd"] = corrected


def fix_buggy_performance_end_times(data: dict) -> dict:
    """
    Override timeEnd on performances hit by the upstream 2h59m bug.

    Only a timeEnd exactly 2h59m after time is treated as buggy, so genuinely
    longer sessions are left untouched.

    Args:
        data: Response data dictionary

    Returns:
        dict: Response data with buggy timeEnd values corrected
    """
    if not isinstance(data, dict) or "days" not in data:
        return data

    days = data.get("days", [])
    if not isinstance(days, list):
        return data

    for day in days:
        if not isinstance(day, dict):
            continue

        performances = day.get("performances", [])
        if not isinstance(performances, list):
            continue

        for performance in performances:
            _fix_performance_time_end(performance)

    return data
