import json
import logging
import copy
from pathlib import Path
from datetime import datetime, timedelta
import requests

logger = logging.getLogger(__name__)


def get_calendar(date_from: str, number_of_days: str) -> dict:
    """
    Fetch calendar events from the upstream API.
    
    Args:
        date_from: Start date in YYYY-MM-DD format
        number_of_days: Number of days to fetch (as string)
    
    Returns:
        dict: JSON response from the API as a dictionary
    """
    # url = "https://ticketing-api.thewave.com/api/twb-prod/b2c/v1/events/calendar"
    url = "http://localhost:5005/api/twb-prod/b2c/v1/events/calendar"
    
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
