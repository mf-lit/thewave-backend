"""Calendar API client for fetching session availability data."""
import logging
import os
from typing import Dict, List, Optional

import requests
import yaml

from .config import get_config_path

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30


def _get_calendar_api_url() -> str:
    """Get the calendar API base URL from environment."""
    return os.getenv("CALENDAR_API_URL", "http://localhost:5000/calendar")


def _get_calendar_api_key() -> Optional[str]:
    """Get the calendar API key from environment or config file.
    
    First checks the CALENDAR_API_KEY environment variable.
    If unset, loads from config/config.yaml under key 'calendar_api_key'.
    """
    # First check environment variable
    api_key = os.getenv("CALENDAR_API_KEY")
    if api_key:
        return api_key
    
    # Fall back to config file
    try:
        config_path = get_config_path()
        
        if config_path.exists():
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)
            
            if config:
                api_key = config.get("calendar_api_key")
                if api_key:
                    logger.debug("Loaded calendar API key from config file")
                    return api_key
    except Exception as e:
        logger.warning(f"Failed to load calendar API key from config file: {e}")
    
    return None


def fetch_calendar_data(date_from: str, number_of_days: int) -> Dict:
    """Fetch calendar data from the upstream API."""
    headers = {}
    api_key = _get_calendar_api_key()
    if api_key:
        headers["x-api-key"] = api_key
    
    response = requests.get(
        _get_calendar_api_url(),
        params={"dateFrom": date_from, "numberOfDays": number_of_days},
        headers=headers,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def fetch_calendar_data_for_dates(dates: List[str]) -> Dict:
    """Fetch calendar data for specific dates only.

    Makes individual API calls for each date to minimize data fetched.
    """
    if not dates:
        return {"days": []}

    unique_dates = sorted(set(dates))
    base_url = _get_calendar_api_url()
    all_days = []
    
    headers = {}
    api_key = _get_calendar_api_key()
    if api_key:
        headers["x-api-key"] = api_key

    for date in unique_dates:
        response = requests.get(base_url, params={"dateFrom": date, "numberOfDays": 1}, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        for day in data.get("days", []):
            if day.get("date") == date:
                all_days.append(day)

    return {"days": all_days}


def _normalize_time(time_str: str) -> Optional[str]:
    """Normalize time to HH:MM format, stripping seconds/milliseconds."""
    if not time_str:
        return None
    parts = time_str.split(":")
    if len(parts) < 2:
        return None
    return f"{parts[0]}:{parts[1]}"


def find_performance_by_date_time_side(
    calendar_data: Dict, date: str, time: str, side: str
) -> Optional[Dict]:
    """Find a performance by date, time, and side."""
    normalized_time = _normalize_time(time)
    if not normalized_time:
        return None

    for day in calendar_data.get("days", []):
        if day.get("date") != date:
            continue

        for performance in day.get("performances", []):
            perf_normalized = _normalize_time(performance.get("time", ""))
            if perf_normalized != normalized_time:
                continue

            for product in performance.get("availabilityPerProduct", []):
                if product.get("side") == side:
                    return performance

    return None


def find_performance_by_ak(calendar_data: Dict, performance_ak: str) -> Optional[Dict]:
    """Find a performance by performanceAK."""
    for day in calendar_data.get("days", []):
        for performance in day.get("performances", []):
            if performance.get("performanceAK") == performance_ak:
                return performance
    return None


def extract_availability_by_side(performance: Dict, side: str) -> Optional[int]:
    """Extract availability count for a specific side from a performance."""
    for product in performance.get("availabilityPerProduct", []):
        if product.get("side") == side:
            return product.get("availability", {}).get("available")
    return None


def get_performance_title(performance: Dict) -> str:
    """Get the title of a performance."""
    return performance.get("fields", {}).get("title", "")


def get_notification_dates(notifications: List[Dict]) -> List[str]:
    """Extract unique sorted dates from notifications."""
    if not notifications:
        return []
    dates = [n["date"] for n in notifications if n.get("date")]
    return sorted(set(dates))

