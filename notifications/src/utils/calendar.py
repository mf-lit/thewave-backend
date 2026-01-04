"""Calendar API client for fetching session availability data."""
import os
from typing import Dict, List, Optional

import requests


def fetch_calendar_data(date_from: str, number_of_days: int) -> Dict:
    """Fetch calendar data from the upstream API.

    Args:
        date_from: Start date in YYYY-MM-DD format
        number_of_days: Number of days to fetch

    Returns:
        Calendar data dictionary with days and performances

    Raises:
        requests.RequestException: If the API request fails
    """
    base_url = os.getenv("CALENDAR_API_URL", "http://localhost:5000/calendar")
    params = {"dateFrom": date_from, "numberOfDays": number_of_days}

    response = requests.get(base_url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_calendar_data_for_dates(dates: List[str]) -> Dict:
    """Fetch calendar data for specific dates only.
    
    Makes individual API calls for each date to minimize data fetched.
    Merges the results into a single calendar data structure.

    Args:
        dates: List of dates in YYYY-MM-DD format to fetch

    Returns:
        Calendar data dictionary with days and performances for the specified dates

    Raises:
        requests.RequestException: If any API request fails
    """
    if not dates:
        return {"days": []}
    
    # Remove duplicates and sort
    unique_dates = sorted(set(dates))
    
    # Fetch each date individually (numberOfDays=1)
    all_days = []
    base_url = os.getenv("CALENDAR_API_URL", "http://localhost:5000/calendar")
    
    for date in unique_dates:
        params = {"dateFrom": date, "numberOfDays": 1}
        response = requests.get(base_url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        # Extract the day data for this specific date
        days = data.get("days", [])
        for day in days:
            if day.get("date") == date:
                all_days.append(day)
    
    return {"days": all_days}


def find_performance_by_date_time_side(
    calendar_data: Dict, date: str, time: str, side: str
) -> Optional[Dict]:
    """Find a performance by date, time, and side.

    Args:
        calendar_data: Calendar data from fetch_calendar_data
        date: Date in YYYY-MM-DD format
        time: Time in HH:MM format
        side: Side identifier ("left", "right", or "none")

    Returns:
        Performance dictionary if found, None otherwise
    """
    # Normalize time format (handle both HH:MM and HH:MM:SS.mmm)
    time_parts = time.split(":")
    normalized_time = f"{time_parts[0]}:{time_parts[1]}"

    for day in calendar_data.get("days", []):
        if day.get("date") != date:
            continue

        for performance in day.get("performances", []):
            perf_time = performance.get("time", "")
            # Normalize performance time (strip milliseconds)
            if perf_time:
                perf_time_parts = perf_time.split(":")
                if len(perf_time_parts) >= 2:
                    perf_normalized = f"{perf_time_parts[0]}:{perf_time_parts[1]}"
                else:
                    continue
            else:
                continue

            if perf_normalized != normalized_time:
                continue

            # Check if this performance has the requested side
            availability_per_product = performance.get("availabilityPerProduct", [])
            for product in availability_per_product:
                if product.get("side") == side:
                    return performance

    return None


def find_performance_by_ak(calendar_data: Dict, performance_ak: str) -> Optional[Dict]:
    """Find a performance by performanceAK.

    Args:
        calendar_data: Calendar data from fetch_calendar_data
        performance_ak: Performance identifier (e.g., "TWB.EVN10.PRF1487")

    Returns:
        Performance dictionary if found, None otherwise
    """
    for day in calendar_data.get("days", []):
        for performance in day.get("performances", []):
            if performance.get("performanceAK") == performance_ak:
                return performance
    return None


def extract_availability_by_side(performance: Dict, side: str) -> Optional[int]:
    """Extract availability count for a specific side from a performance.

    Args:
        performance: Performance dictionary
        side: Side identifier ("left", "right", or "none")

    Returns:
        Availability count if found, None otherwise
    """
    availability_per_product = performance.get("availabilityPerProduct", [])
    for product in availability_per_product:
        if product.get("side") == side:
            availability = product.get("availability", {})
            return availability.get("available")
    return None


def get_performance_title(performance: Dict) -> str:
    """Get the title of a performance.

    Args:
        performance: Performance dictionary

    Returns:
        Performance title or empty string
    """
    fields = performance.get("fields", {})
    return fields.get("title", "")


def get_notification_dates(notifications: List[Dict]) -> List[str]:
    """Extract unique dates from notifications.
    
    Args:
        notifications: List of notification dictionaries
        
    Returns:
        List of unique dates in YYYY-MM-DD format
    """
    if not notifications:
        return []
    
    dates = [n["date"] for n in notifications if "date" in n and n["date"]]
    return sorted(set(dates))

