#!/usr/bin/env python3
"""
Script that dumps the entire contents of the water temperature database.
Outputs data in CSV format by default, or JSON format with --json flag.
"""

import argparse
import csv
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database file path (relative to project root)
def get_db_path():
    """Get the path to the water temperature database."""
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    db_path = project_root / "data" / "water_temperature.db"
    return db_path


def get_all_temperatures():
    """
    Retrieve all temperature records from the database.
    
    Returns:
        list[dict]: List of temperature records
    """
    db_path = get_db_path()
    
    if not db_path.exists():
        logger.error(f"Database file not found: {db_path}")
        sys.exit(1)
    
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT id, temperature, recorded_at, created_at
            FROM water_temperature
            ORDER BY recorded_at ASC
        """)
        
        rows = cursor.fetchall()
        records = [
            {
                "id": row["id"],
                "temperature": row["temperature"],
                "recorded_at": row["recorded_at"],
                "created_at": row["created_at"]
            }
            for row in rows
        ]
        
        conn.close()
        return records
    
    except sqlite3.Error as e:
        logger.error(f"Database error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)


def output_csv(records, output_file=None):
    """
    Output records in CSV format.
    
    Args:
        records: List of temperature records
        output_file: Optional output file path. If None, outputs to stdout.
    """
    if not records:
        logger.warning("No records found in database")
        return
    
    fieldnames = ["id", "temperature", "recorded_at", "created_at"]
    
    if output_file:
        with open(output_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)
        logger.info(f"Exported {len(records)} records to {output_file}")
    else:
        writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def output_json(records, output_file=None, pretty=False):
    """
    Output records in JSON format.
    
    Args:
        records: List of temperature records
        output_file: Optional output file path. If None, outputs to stdout.
        pretty: If True, pretty-print JSON with indentation
    """
    if not records:
        logger.warning("No records found in database")
        return
    
    json_data = {
        "total_records": len(records),
        "records": records
    }
    
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            if pretty:
                json.dump(json_data, f, indent=2, ensure_ascii=False)
            else:
                json.dump(json_data, f, ensure_ascii=False)
        logger.info(f"Exported {len(records)} records to {output_file}")
    else:
        if pretty:
            json.dump(json_data, sys.stdout, indent=2, ensure_ascii=False)
        else:
            json.dump(json_data, sys.stdout, ensure_ascii=False)
        print()  # Add newline after JSON output


def output_summary(records):
    """
    Output a summary of the database contents.
    
    Args:
        records: List of temperature records
    """
    if not records:
        logger.warning("No records found in database")
        return
    
    temperatures = [r["temperature"] for r in records]
    
    print("\n" + "=" * 60)
    print("Temperature Database Summary")
    print("=" * 60)
    print(f"Total records: {len(records)}")
    
    if records:
        earliest = min(records, key=lambda r: r["recorded_at"])
        latest = max(records, key=lambda r: r["recorded_at"])
        print(f"Date range: {earliest['recorded_at']} to {latest['recorded_at']}")
        print(f"Temperature range: {min(temperatures):.1f}°C to {max(temperatures):.1f}°C")
        print(f"Average temperature: {sum(temperatures) / len(temperatures):.2f}°C")
    
    print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Dump the entire contents of the water temperature database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Output to stdout as CSV (default)
  python dump_temperature_db.py

  # Output to file as CSV
  python dump_temperature_db.py --output temperatures.csv

  # Output as JSON
  python dump_temperature_db.py --json

  # Output as pretty-printed JSON to file
  python dump_temperature_db.py --json --pretty --output temperatures.json

  # Show summary only
  python dump_temperature_db.py --summary
        """
    )
    
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in JSON format instead of CSV"
    )
    
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output (only applies with --json)"
    )
    
    parser.add_argument(
        "-o", "--output",
        type=str,
        help="Output file path. If not specified, outputs to stdout."
    )
    
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Show summary statistics only"
    )
    
    args = parser.parse_args()
    
    # Get all records from database
    logger.info("Reading temperature database...")
    records = get_all_temperatures()
    
    if args.summary:
        output_summary(records)
    elif args.json:
        output_json(records, args.output, args.pretty)
    else:
        output_csv(records, args.output)
    
    logger.info(f"Successfully processed {len(records)} records")


if __name__ == "__main__":
    main()
