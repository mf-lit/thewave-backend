#!/usr/bin/env python3
"""
Emulate the upstream API by time-shifting requests.

This script acts as a proxy that shifts dates in requests to the upstream API,
allowing you to test with future/past dates using real data.

Usage:
    python emulate_upstream.py --timeshift-days 10 --port 5001

Example:
    Request to http://localhost:5001/api/twb-prod/b2c/v1/events/calendar?dateFrom=2026-01-18
    Will fetch from upstream with dateFrom=2026-01-28 (shifted by 10 days)
"""

# Fix for "code for hash blake2b was not found" error
# This is a known issue with Python/OpenSSL on some systems (especially macOS)
# The error is logged but doesn't prevent the program from running
import logging

# Suppress the specific error message from hashlib
class Blake2bErrorFilter(logging.Filter):
    def filter(self, record):
        return 'blake2b' not in record.getMessage().lower()

# Apply filter to root logger to catch the error
logging.getLogger().addFilter(Blake2bErrorFilter())

# Also try to patch hashlib to prevent the error from occurring
try:
    import hashlib
    # Pre-emptively try to load blake2b to see if it's available
    # If it fails, we'll catch it here rather than in dependencies
    try:
        _ = hashlib.blake2b
    except (AttributeError, ValueError, OSError):
        # blake2b is not available, which is fine - we don't need it
        pass
except Exception:
    # If there's any error, ignore it - dependencies will handle it
    pass

import argparse
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# Configuration
TIMESHIFT_DAYS = 0  # Will be set via command line
UPSTREAM_BASE_URL = "https://ticketing-api.thewave.com"


def shift_date(date_str: str, days: int) -> str:
    """
    Shift a date string by a number of days.

    Args:
        date_str: Date in YYYY-MM-DD format
        days: Number of days to shift (positive for future, negative for past)

    Returns:
        str: Shifted date in YYYY-MM-DD format
    """
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        shifted_date = date_obj + timedelta(days=days)
        return shifted_date.strftime("%Y-%m-%d")
    except ValueError as e:
        logger.error(f"Error parsing date {date_str}: {e}")
        return date_str


def unshift_date_in_response(data: dict, days: int) -> dict:
    """
    Recursively unshift dates in the response data.

    Args:
        data: Response data from upstream API
        days: Number of days to shift back (opposite of request shift)

    Returns:
        dict: Response with dates shifted back to requested dates
    """
    if isinstance(data, dict):
        result = {}
        for key, value in data.items():
            # Shift date fields back
            if key == "date" and isinstance(value, str):
                try:
                    result[key] = shift_date(value, -days)
                except Exception:
                    result[key] = value
            else:
                result[key] = unshift_date_in_response(value, days)
        return result
    elif isinstance(data, list):
        return [unshift_date_in_response(item, days) for item in data]
    else:
        return data


@app.route('/api/twb-prod/b2c/v1/events/calendar', methods=['GET'])
def calendar_proxy():
    """
    Proxy endpoint for the calendar API with time-shifting.
    """
    # Get query parameters
    date_from = request.args.get('dateFrom')
    number_of_days = request.args.get('numberOfDays', '1')
    locale = request.args.get('locale', 'en-GB')

    if not date_from:
        return jsonify({"error": "Missing required parameter: dateFrom"}), 400

    # Shift the date for upstream request
    shifted_date = shift_date(date_from, TIMESHIFT_DAYS)

    logger.info(f"Request: dateFrom={date_from} (shifted to {shifted_date}, timeshift={TIMESHIFT_DAYS} days)")

    # Build upstream request
    upstream_url = f"{UPSTREAM_BASE_URL}/api/twb-prod/b2c/v1/events/calendar"

    params = [
        ("locale", locale),
        ("eventCategoryCode[]", "TWBB2C"),
        ("eventCategoryCode[]", "ALL2"),
        ("dateFrom", shifted_date),
        ("numberOfDays", number_of_days)
    ]

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

    try:
        # Make request to upstream API
        response = requests.get(upstream_url, params=params, headers=headers, timeout=30)
        response.raise_for_status()

        # Get JSON response
        data = response.json()

        # Shift dates back in response
        shifted_data = unshift_date_in_response(data, TIMESHIFT_DAYS)

        logger.info(f"Successfully fetched and shifted response for {date_from}")

        return jsonify(shifted_data), 200

    except requests.exceptions.RequestException as e:
        logger.error(f"Upstream API request failed: {e}")
        return jsonify({"error": f"Upstream API error: {str(e)}"}), 502
    except ValueError as e:
        logger.error(f"Invalid JSON response: {e}")
        return jsonify({"error": "Invalid response from upstream API"}), 502


@app.route('/health', methods=['GET'])
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "timeshift_days": TIMESHIFT_DAYS,
        "upstream_url": UPSTREAM_BASE_URL
    }), 200


def main():
    parser = argparse.ArgumentParser(
        description='Emulate upstream API with time-shifting',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Shift dates 10 days into the future
  python emulate_upstream.py --timeshift-days 10

  # Shift dates 5 days into the past
  python emulate_upstream.py --timeshift-days -5

  # Run on custom port
  python emulate_upstream.py --timeshift-days 10 --port 5001

  # Test the emulator
  curl "http://localhost:5000/api/twb-prod/b2c/v1/events/calendar?dateFrom=2026-01-18&numberOfDays=1"
        """
    )

    parser.add_argument(
        '--timeshift-days',
        type=int,
        default=0,
        help='Number of days to shift dates (positive=future, negative=past, default=0)'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=5000,
        help='Port to run the emulator on (default=5000)'
    )

    parser.add_argument(
        '--host',
        type=str,
        default='0.0.0.0',
        help='Host to bind to (default=0.0.0.0)'
    )

    parser.add_argument(
        '--debug',
        action='store_true',
        help='Enable debug mode'
    )

    args = parser.parse_args()

    # Set global timeshift
    global TIMESHIFT_DAYS
    TIMESHIFT_DAYS = args.timeshift_days

    # Print configuration
    print("=" * 60)
    print("Upstream API Emulator")
    print("=" * 60)
    print(f"Timeshift: {TIMESHIFT_DAYS} days")
    print(f"Host: {args.host}")
    print(f"Port: {args.port}")
    print(f"Upstream: {UPSTREAM_BASE_URL}")
    print("=" * 60)
    print(f"\nEmulator URL: http://{args.host}:{args.port}")
    print(f"Health check: http://{args.host}:{args.port}/health")
    print(f"Calendar endpoint: http://{args.host}:{args.port}/api/twb-prod/b2c/v1/events/calendar")
    print("=" * 60)
    print("\nExample usage:")
    print(f'  curl "http://localhost:{args.port}/api/twb-prod/b2c/v1/events/calendar?dateFrom=2026-01-18&numberOfDays=1"')
    print("\nTo use with your API, update UPSTREAM_API_URL in wave_calendar.py to:")
    print(f'  http://localhost:{args.port}')
    print("=" * 60)
    print("\nStarting server...\n")

    # Run the Flask app
    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug
    )


if __name__ == '__main__':
    main()
