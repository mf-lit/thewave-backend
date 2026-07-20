#!/usr/bin/env python3
"""Script to pretty print all notifications with their associated client information."""
import logging
from typing import Dict, List, Any, Optional
from tabulate import tabulate
from src.storage.dynamodb import DynamoDBStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_all_clients(storage: DynamoDBStorage) -> Dict[str, Dict[str, Any]]:
    """Get all clients from the clients table and return as a dictionary keyed by client_id.
    
    Args:
        storage: DynamoDB storage instance
        
    Returns:
        Dictionary mapping client_id to client record
    """
    storage.ensure_clients_table_exists()
    clients_table = storage.dynamodb.Table("waveform-clients")
    
    clients = {}
    response = clients_table.scan()
    for item in response.get("Items", []):
        client_id = item.get("client_id")
        if client_id:
            clients[client_id] = item
    
    # Handle pagination
    while "LastEvaluatedKey" in response:
        response = clients_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        for item in response.get("Items", []):
            client_id = item.get("client_id")
            if client_id:
                clients[client_id] = item
    
    return clients


def truncate_token(token: Optional[str], max_length: int = 30) -> str:
    """Truncate FCM token for display.
    
    Args:
        token: FCM token string
        max_length: Maximum length to display
        
    Returns:
        Truncated token with ellipsis if needed
    """
    if not token:
        return "N/A"
    if len(token) <= max_length:
        return token
    return token[:max_length] + "..."


def format_thresholds(thresholds: Optional[List[int]]) -> str:
    """Format thresholds list for display.
    
    Args:
        thresholds: List of threshold values
        
    Returns:
        Formatted string representation
    """
    if not thresholds:
        return "N/A"
    return ", ".join(str(t) for t in thresholds)


def format_notified_thresholds(notified_thresholds: Optional[List[int]]) -> str:
    """Format notified_thresholds list for display.
    
    Args:
        notified_thresholds: List of notified threshold values
        
    Returns:
        Formatted string representation
    """
    if not notified_thresholds:
        return "None"
    return ", ".join(str(t) for t in notified_thresholds)


def main():
    """Pretty print all notifications with their associated client information."""
    storage = DynamoDBStorage()
    
    # Ensure tables exist
    logger.info("Ensuring tables exist...")
    storage.ensure_table_exists()
    storage.ensure_clients_table_exists()
    
    # Get all notifications
    logger.info("Fetching all notifications...")
    notifications = storage.get_all_notifications()
    logger.info(f"Found {len(notifications)} total notifications")
    
    if not notifications:
        logger.info("No notifications found in the database.")
        return
    
    # Get all clients
    logger.info("Fetching all clients...")
    clients = get_all_clients(storage)
    logger.info(f"Found {len(clients)} total clients")
    
    # Prepare table data
    table_data = []
    for notification in notifications:
        client_id = notification.get("client_id", "N/A")
        client_info = clients.get(client_id, {})
        
        row = [
            client_id,
            notification.get("notification_id", "N/A"),
            notification.get("performance_ak", "N/A"),
            notification.get("date", "N/A"),
            notification.get("time", "N/A"),
            notification.get("side", "N/A"),
            notification.get("notification_type", "N/A"),
            format_thresholds(notification.get("thresholds")),
            format_notified_thresholds(notification.get("notified_thresholds")),
            truncate_token(client_info.get("fcm_token")),
            client_info.get("updated_at", "N/A"),
            notification.get("created_at", "N/A"),
            notification.get("last_checked_availability", "N/A"),
        ]
        table_data.append(row)
    
    # Define table headers
    headers = [
        "Client ID",
        "Notification ID",
        "Performance AK",
        "Date",
        "Time",
        "Side",
        "Type",
        "Thresholds",
        "Notified Thresholds",
        "FCM Token",
        "Client Updated",
        "Created At",
        "Last Checked",
    ]
    
    # Print the table
    print("\n" + "=" * 120)
    print("NOTIFICATIONS DATABASE")
    print("=" * 120 + "\n")
    print(tabulate(table_data, headers=headers, tablefmt="grid"))
    
    # Print summary
    print("\n" + "=" * 120)
    print("SUMMARY")
    print("=" * 120)
    print(f"Total notifications: {len(notifications)}")
    print(f"Total clients: {len(clients)}")
    print(f"Notifications with client records: {sum(1 for n in notifications if n.get('client_id') in clients)}")
    print(f"Notifications without client records: {sum(1 for n in notifications if n.get('client_id') not in clients)}")
    print("=" * 120 + "\n")


if __name__ == "__main__":
    main()
