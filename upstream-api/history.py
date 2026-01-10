import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
import copy

logger = logging.getLogger(__name__)


def _get_history_dir() -> Path:
    """
    Get the history directory path.
    
    Returns:
        Path: Path to the history directory
    """
    script_dir = Path(__file__).parent
    history_dir = script_dir / "history"
    return history_dir


def normalize_calendar_response(data: dict, date_from: str, number_of_days: int = 1) -> dict:
    """
    Normalize calendar response data to ensure it has the expected structure.
    Handles cases where the API returns "No schedule data available" or other formats.
    This ensures the response always has a proper days[] array structure.
    
    Args:
        data: Response data dictionary
        date_from: Start date in YYYY-MM-DD format
        number_of_days: Number of days in the response
    
    Returns:
        dict: Normalized response with proper structure
    """
    from datetime import datetime, timedelta
    
    # If it's already in the correct format with days array, ensure it's properly structured
    if isinstance(data, dict) and "days" in data:
        days = data.get("days", [])
        if not isinstance(days, list):
            days = []
        
        # Ensure we have the right number of days
        base_date = datetime.strptime(date_from, "%Y-%m-%d")
        normalized_days = []
        
        for day_offset in range(number_of_days):
            current_date = base_date + timedelta(days=day_offset)
            current_date_str = current_date.strftime("%Y-%m-%d")
            
            # Try to find a day for this date
            day_found = False
            for day in days:
                if isinstance(day, dict) and day.get("date") == current_date_str:
                    normalized_days.append(copy.deepcopy(day))
                    day_found = True
                    break
            
            # If no day found for this date, create an empty day structure
            if not day_found:
                normalized_days.append({
                    "availability": None,
                    "date": current_date_str,
                    "hasNoTimeSlot": None,
                    "performances": [],
                    "priceMax": None,
                    "priceMin": None
                })
        
        return {"days": normalized_days}
    
    # If it's not in the expected format, create a normalized structure
    logger.warning(f"Response data for {date_from} is not in expected format, normalizing")
    base_date = datetime.strptime(date_from, "%Y-%m-%d")
    normalized_days = []
    
    for day_offset in range(number_of_days):
        current_date = base_date + timedelta(days=day_offset)
        current_date_str = current_date.strftime("%Y-%m-%d")
        normalized_days.append({
            "availability": None,
            "date": current_date_str,
            "hasNoTimeSlot": None,
            "performances": [],
            "priceMax": None,
            "priceMin": None
        })
    
    return {"days": normalized_days}


def _normalize_response_data(data: dict, date: str) -> dict:
    """
    Normalize response data to ensure it has the expected structure.
    Handles cases where the API returns "No schedule data available" or other formats.
    
    Args:
        data: Response data dictionary
        date: Date in YYYY-MM-DD format
    
    Returns:
        dict: Normalized response with proper structure
    """
    return normalize_calendar_response(data, date, 1)


def save_daily_history(date: str, data: dict) -> None:
    """
    Save a day's response to history/YYYY-MM-DD.json.
    Normalizes the data to ensure it has the expected structure.
    
    Args:
        date: Date in YYYY-MM-DD format
        data: Response data dictionary to save
    """
    history_dir = _get_history_dir()
    history_dir.mkdir(exist_ok=True)
    
    history_file = history_dir / f"{date}.json"
    
    logger.info(f"Saving daily history for {date} to {history_file}")
    
    # Normalize the data before saving
    normalized_data = _normalize_response_data(data, date)
    
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(normalized_data, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Successfully saved history for {date}")


def load_historical_day(date: str) -> dict | None:
    """
    Load a specific day's historical file.
    
    Args:
        date: Date in YYYY-MM-DD format
    
    Returns:
        dict | None: Historical data if file exists, None otherwise
    """
    history_dir = _get_history_dir()
    history_file = history_dir / f"{date}.json"
    
    if not history_file.exists():
        return None
    
    try:
        logger.info(f"Loading historical data for {date} from {history_file}")
        with open(history_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load historical data for {date}: {str(e)}")
        return None


def build_multi_day_response(date_from: str, number_of_days: int) -> dict:
    """
    Build multi-day response from individual historical files.
    
    Args:
        date_from: Start date in YYYY-MM-DD format
        number_of_days: Number of days to fetch
    
    Returns:
        dict: Combined response with days[] array, may be partial if some files are missing
    """
    base_date = datetime.strptime(date_from, "%Y-%m-%d")
    
    all_days = []
    missing_days = []
    
    for day_offset in range(number_of_days):
        current_date = base_date + timedelta(days=day_offset)
        current_date_str = current_date.strftime("%Y-%m-%d")
        
        day_data = load_historical_day(current_date_str)
        
        if day_data is None:
            missing_days.append(current_date_str)
            logger.warning(f"Historical data missing for {current_date_str}")
            # Create an empty day structure to maintain response format
            empty_day = {
                "availability": None,
                "date": current_date_str,
                "hasNoTimeSlot": None,
                "performances": [],
                "priceMax": None,
                "priceMin": None
            }
            all_days.append(empty_day)
            continue
        
        # Extract days from the historical response
        if isinstance(day_data, dict) and "days" in day_data:
            days_list = day_data.get("days", [])
            if isinstance(days_list, list) and len(days_list) > 0:
                # Take the first day from the historical file (should be the requested date)
                all_days.append(copy.deepcopy(days_list[0]))
            else:
                # Empty days array - create a day structure with no performances
                logger.info(f"Historical data for {current_date_str} has no schedule data")
                empty_day = {
                    "availability": None,
                    "date": current_date_str,
                    "hasNoTimeSlot": None,
                    "performances": [],
                    "priceMax": None,
                    "priceMin": None
                }
                all_days.append(empty_day)
        else:
            # Unexpected format - create empty day structure to maintain response format
            logger.warning(f"Unexpected format in historical data for {current_date_str}, creating empty day structure")
            empty_day = {
                "availability": None,
                "date": current_date_str,
                "hasNoTimeSlot": None,
                "performances": [],
                "priceMax": None,
                "priceMin": None
            }
            all_days.append(empty_day)
    
    # Build response in same format as upstream API
    response = {
        "days": all_days
    }
    
    # Add warning message if some days are missing
    if missing_days:
        response["_warnings"] = {
            "missing_days": missing_days,
            "message": f"Historical data missing for {len(missing_days)} day(s): {', '.join(missing_days)}"
        }
        logger.warning(f"Partial historical response: {len(missing_days)} day(s) missing out of {number_of_days} requested")
    
    return response
