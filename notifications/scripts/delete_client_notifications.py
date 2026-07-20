#!/usr/bin/env python3
"""Script to delete all notifications for a given client ID."""
import logging
import sys
from src.storage.dynamodb import DynamoDBStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Delete all notifications for a given client ID."""
    # Get client_id from command line argument
    if len(sys.argv) < 2:
        logger.error("Usage: python delete_client_notifications.py <client_id>")
        logger.error("Example: python delete_client_notifications.py 123e4567-e89b-12d3-a456-426614174000")
        sys.exit(1)
    
    client_id = sys.argv[1].strip()
    
    if not client_id:
        logger.error("Client ID cannot be empty")
        sys.exit(1)
    
    storage = DynamoDBStorage()
    
    # Ensure table exists
    logger.info("Ensuring table exists...")
    storage.ensure_table_exists()
    
    # Get all notifications for this client
    logger.info(f"Fetching notifications for client {client_id}...")
    notifications = storage.get_notifications_by_client(client_id)
    logger.info(f"Found {len(notifications)} notifications for client {client_id}")
    
    if not notifications:
        logger.info(f"No notifications found for client {client_id}. Nothing to delete.")
        return
    
    # Show what will be deleted
    logger.info("\nNotifications to be deleted:")
    for notification in notifications:
        notification_id = notification.get("notification_id", "N/A")
        performance_ak = notification.get("performance_ak", "N/A")
        date = notification.get("date", "N/A")
        time = notification.get("time", "N/A")
        logger.info(
            f"  - {notification_id}: {performance_ak} ({date} {time})"
        )
    
    # Confirm deletion
    logger.info(f"\nAbout to delete {len(notifications)} notification(s) for client {client_id}")
    response = input("Are you sure you want to proceed? (yes/no): ").strip().lower()
    
    if response != "yes":
        logger.info("Deletion cancelled.")
        return
    
    # Delete notifications
    deleted_count = 0
    failed_count = 0
    
    logger.info(f"\nDeleting {len(notifications)} notification(s)...")
    for notification in notifications:
        notification_id = notification.get("notification_id")
        
        if not notification_id:
            logger.warning(f"Skipping notification with missing notification_id: {notification}")
            failed_count += 1
            continue
        
        try:
            success = storage.delete_notification(client_id, notification_id)
            if success:
                deleted_count += 1
                logger.info(f"Deleted notification {notification_id}")
            else:
                failed_count += 1
                logger.warning(f"Failed to delete notification {notification_id} (not found)")
        except Exception as e:
            failed_count += 1
            logger.error(
                f"Error deleting notification {notification_id}: {e}",
                exc_info=True
            )
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("DELETION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Client ID: {client_id}")
    logger.info(f"Total notifications found: {len(notifications)}")
    logger.info(f"Successfully deleted: {deleted_count}")
    logger.info(f"Failed: {failed_count}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
