#!/usr/bin/env python3
"""Script to clear notified_thresholds from all notifications."""
import logging
from src.storage.dynamodb import DynamoDBStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def main():
    """Clear notified_thresholds from all notifications."""
    storage = DynamoDBStorage()
    
    # Ensure table exists
    storage.ensure_table_exists()
    
    # Get all notifications
    logger.info("Fetching all notifications...")
    notifications = storage.get_all_notifications()
    logger.info(f"Found {len(notifications)} total notifications")
    
    if not notifications:
        logger.info("No notifications found. Exiting.")
        return
    
    # Filter notifications that have notified_thresholds
    notifications_with_thresholds = [
        n for n in notifications 
        if n.get("notification_type") == "below_threshold" and n.get("notified_thresholds")
    ]
    
    logger.info(f"Found {len(notifications_with_thresholds)} notifications with notified_thresholds to clear")
    
    if not notifications_with_thresholds:
        logger.info("No notifications with notified_thresholds found. Nothing to clear.")
        return
    
    # Clear notified_thresholds for each notification
    cleared_count = 0
    failed_count = 0
    
    for notification in notifications_with_thresholds:
        client_id = notification.get("client_id")
        notification_id = notification.get("notification_id")
        current_thresholds = notification.get("notified_thresholds", [])
        
        if not client_id or not notification_id:
            logger.warning(f"Skipping notification with missing client_id or notification_id: {notification}")
            continue
        
        try:
            # Clear notified_thresholds by setting it to an empty list
            storage.update_notification(
                client_id,
                notification_id,
                {"notified_thresholds": []},
            )
            cleared_count += 1
            logger.info(
                f"Cleared notified_thresholds for notification {notification_id} "
                f"(client: {client_id}, had {len(current_thresholds)} thresholds: {current_thresholds})"
            )
        except Exception as e:
            failed_count += 1
            logger.error(
                f"Failed to clear notified_thresholds for notification {notification_id} "
                f"(client: {client_id}): {e}"
            )
    
    logger.info(f"\n=== Summary ===")
    logger.info(f"  Notifications processed: {len(notifications_with_thresholds)}")
    logger.info(f"  Successfully cleared: {cleared_count}")
    logger.info(f"  Failed: {failed_count}")


if __name__ == "__main__":
    main()

