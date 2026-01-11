#!/usr/bin/env python3
"""
Script that periodically modifies data/response.json by randomly selecting
an availabilityPerProduct availability value and either decreasing it by 1
(if > 0) or increasing it to 1 (if == 0).
"""

import argparse
import json
import logging
import random
import signal
import sys
import time
from pathlib import Path


# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
running = True


def signal_handler(sig, frame):
    """Handle SIGINT (Ctrl+C) and SIGTERM for graceful shutdown."""
    global running
    logger.info("Received shutdown signal, stopping...")
    running = False


def interruptible_sleep(duration):
    """
    Sleep for the specified duration, but check the running flag periodically
    to allow immediate exit on interrupt.
    
    Args:
        duration: Number of seconds to sleep
    """
    global running
    # Sleep in 0.5 second chunks to allow quick response to interrupts
    chunk_size = 0.5
    elapsed = 0.0
    while elapsed < duration and running:
        sleep_time = min(chunk_size, duration - elapsed)
        time.sleep(sleep_time)
        elapsed += sleep_time


def find_availability_values(data):
    """
    Find all availabilityPerProduct[].availability.available values in the JSON structure.
    
    Args:
        data: The JSON data structure (dict or list)
    
    Returns:
        list: List of tuples (availability_dict, 'available', current_value, performance_info) for each found value
        performance_info is a dict with keys: performanceAK, eventAk, title, date, time, product_code
    """
    availability_values = []
    
    # Traverse the structure: days[] -> performances[] -> availabilityPerProduct[]
    if isinstance(data, dict) and 'days' in data:
        days = data['days']
        if isinstance(days, list):
            for day in days:
                day_date = day.get('date') if isinstance(day, dict) else None
                if isinstance(day, dict) and 'performances' in day:
                    performances = day['performances']
                    if isinstance(performances, list):
                        for performance in performances:
                            if isinstance(performance, dict) and 'availabilityPerProduct' in performance:
                                # Extract performance info
                                performance_info = {
                                    'performanceAK': performance.get('performanceAK'),
                                    'eventAk': performance.get('eventAk'),
                                    'title': performance.get('fields', {}).get('title') if isinstance(performance.get('fields'), dict) else None,
                                    'date': performance.get('date', day_date),
                                    'time': performance.get('time'),
                                }
                                
                                products = performance['availabilityPerProduct']
                                if isinstance(products, list):
                                    for product in products:
                                        if isinstance(product, dict) and 'availability' in product:
                                            avail = product['availability']
                                            if isinstance(avail, dict) and 'available' in avail:
                                                # Include product code in performance info
                                                product_info = {**performance_info, 'product_code': product.get('code')}
                                                # Store reference to the availability dict
                                                availability_values.append((avail, 'available', avail['available'], product_info))
    
    return availability_values


def modify_random_availability(file_path, interval):
    """
    Main loop that modifies data/response.json every N seconds.
    
    Args:
        file_path: Path to response.json file
        interval: Interval in seconds between modifications
    """
    global running
    
    logger.info(f"Starting availability modifier script (interval: {interval} seconds)")
    logger.info(f"Target file: {file_path}")
    
    while running:
        try:
            # Load JSON file
            logger.debug(f"Loading {file_path}")
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Find all availability values
            availability_values = find_availability_values(data)
            
            if not availability_values:
                logger.warning("No availability values found in JSON structure")
                interruptible_sleep(interval)
                continue
            
            # Randomly select one
            selected = random.choice(availability_values)
            avail_dict, key, current_value, performance_info = selected
            
            # Modify the value
            old_value = current_value
            if current_value > 0:
                new_value = current_value - 1
                action = "decreased"
            else:  # current_value == 0
                new_value = 1
                action = "increased"
            
            # Apply the modification
            avail_dict[key] = new_value
            
            # Build performance description for logging
            perf_desc_parts = []
            if performance_info.get('title'):
                perf_desc_parts.append(performance_info['title'])
            if performance_info.get('performanceAK'):
                perf_desc_parts.append(f"({performance_info['performanceAK']})")
            if performance_info.get('product_code'):
                perf_desc_parts.append(f"[{performance_info['product_code']}]")
            if performance_info.get('date') and performance_info.get('time'):
                perf_desc_parts.append(f"on {performance_info['date']} at {performance_info['time']}")
            
            perf_desc = " ".join(perf_desc_parts) if perf_desc_parts else "Unknown performance"
            
            # Write back to file
            logger.info(f"{action.capitalize()} availability from {old_value} to {new_value} for {perf_desc}")
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write('\n')  # Add trailing newline for consistency
            
            # Sleep for the interval
            logger.debug(f"Sleeping for {interval} seconds...")
            interruptible_sleep(interval)
            
        except FileNotFoundError:
            logger.error(f"File not found: {file_path}")
            logger.info("Waiting before retry...")
            interruptible_sleep(interval)
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            logger.info("Waiting before retry...")
            interruptible_sleep(interval)
        except Exception as e:
            logger.error(f"Unexpected error: {e}", exc_info=True)
            logger.info("Waiting before retry...")
            interruptible_sleep(interval)
    
    logger.info("Script stopped")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Modify availability values in data/response.json periodically"
    )
    parser.add_argument(
        '--interval', '-i',
        type=int,
        default=300,
        help='Interval in seconds between modifications (default: 300)'
    )
    parser.add_argument(
        '--file',
        type=str,
        default=None,
        help='Path to response.json file (default: data/response.json at project root)'
    )
    
    args = parser.parse_args()
    
    # Determine file path
    if args.file:
        file_path = Path(args.file)
    else:
        # Default to data/response.json at project root
        # Get project root (go up from scripts/ to project root)
        project_root = Path(__file__).parent.parent
        file_path = project_root / "data" / "response.json"
    
    # Validate interval
    if args.interval <= 0:
        logger.error("Interval must be positive")
        sys.exit(1)
    
    # Validate file exists
    if not file_path.exists():
        logger.error(f"File not found: {file_path}")
        sys.exit(1)
    
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Run the main loop
    try:
        modify_random_availability(file_path, args.interval)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

