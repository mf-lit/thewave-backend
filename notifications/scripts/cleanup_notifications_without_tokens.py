#!/usr/bin/env python3
"""Script to find and delete notifications and clients without FCM tokens."""
import logging
from src.storage.dynamodb import DynamoDBStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def get_all_clients(storage: DynamoDBStorage):
    """Get all clients from the clients table.
    
    Args:
        storage: DynamoDB storage instance
        
    Returns:
        List of client records
    """
    storage.ensure_clients_table_exists()
    clients_table = storage.dynamodb.Table("waveform-clients")
    
    clients = []
    response = clients_table.scan()
    clients.extend(response.get("Items", []))
    
    # Handle pagination
    while "LastEvaluatedKey" in response:
        response = clients_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
        clients.extend(response.get("Items", []))
    
    return clients


def main():
    """Find and delete notifications and clients without FCM tokens."""
    storage = DynamoDBStorage()
    
    # Ensure tables exist
    storage.ensure_table_exists()
    storage.ensure_clients_table_exists()
    
    # Get all notifications
    logger.info("Fetching all notifications...")
    notifications = storage.get_all_notifications()
    logger.info(f"Found {len(notifications)} total notifications")
    
    # Track statistics
    notifications_to_delete = []
    clients_without_tokens = set()
    
    # Check each notification
    for notification in notifications:
        client_id = notification.get("client_id")
        notification_id = notification.get("notification_id")
        
        if not client_id or not notification_id:
            logger.warning(f"Skipping notification with missing client_id or notification_id: {notification}")
            continue
        
        # Check if client has FCM token
        fcm_token = storage.get_client_token(client_id)
        
        if not fcm_token:
            notifications_to_delete.append((client_id, notification_id))
            clients_without_tokens.add(client_id)
            logger.info(
                f"Client {client_id} has no FCM token. "
                f"Notification {notification_id} will be deleted."
            )
    
    # Report findings
    logger.info(f"\nSummary:")
    logger.info(f"  Total notifications: {len(notifications)}")
    logger.info(f"  Clients without FCM tokens: {len(clients_without_tokens)}")
    logger.info(f"  Notifications to delete: {len(notifications_to_delete)}")
    
    # Delete notifications
    deleted_count = 0
    failed_count = 0
    
    if notifications_to_delete:
        logger.info(f"\nDeleting {len(notifications_to_delete)} notifications...")
        for client_id, notification_id in notifications_to_delete:
            try:
                storage.delete_notification(client_id, notification_id)
                deleted_count += 1
                logger.info(f"Deleted notification {notification_id} for client {client_id}")
            except Exception as e:
                failed_count += 1
                logger.error(
                    f"Failed to delete notification {notification_id} for client {client_id}: {e}"
                )
        
        logger.info(f"\nNotification deletion complete:")
        logger.info(f"  Successfully deleted: {deleted_count}")
        logger.info(f"  Failed: {failed_count}")
    else:
        logger.info("No notifications to delete. All notification clients have FCM tokens.")
    
    # Now delete client records without FCM tokens
    logger.info(f"\nChecking clients table for records without valid FCM tokens...")
    
    # Get all clients from the clients table
    all_clients = get_all_clients(storage)
    logger.info(f"Found {len(all_clients)} clients in clients table")
    
    # Find clients with invalid or missing FCM tokens
    clients_to_delete = []
    for client in all_clients:
        client_id = client.get("client_id")
        fcm_token = client.get("fcm_token")
        
        if not client_id:
            logger.warning(f"Skipping client record with missing client_id: {client}")
            continue
        
        # Check if token is missing or empty
        if not fcm_token or not fcm_token.strip():
            clients_to_delete.append(client_id)
            logger.info(f"Client {client_id} has no or empty FCM token. Will be deleted.")
    
    # Also delete client records for clients we identified earlier (even if not in table)
    for client_id in clients_without_tokens:
        if client_id not in clients_to_delete:
            clients_to_delete.append(client_id)
            logger.info(f"Client {client_id} (from notifications) will be deleted from clients table.")
    
    if not clients_to_delete:
        logger.info("No client records to delete. All clients have valid FCM tokens.")
        return
    
    # Delete client records
    logger.info(f"\nDeleting {len(clients_to_delete)} client records...")
    deleted_clients_count = 0
    failed_clients_count = 0
    
    for client_id in clients_to_delete:
        try:
            storage.delete_client_token(client_id)
            deleted_clients_count += 1
            logger.info(f"Deleted client record for {client_id}")
        except Exception as e:
            failed_clients_count += 1
            logger.error(f"Failed to delete client record for {client_id}: {e}")
    
    logger.info(f"\nClient deletion complete:")
    logger.info(f"  Successfully deleted: {deleted_clients_count}")
    logger.info(f"  Failed: {failed_clients_count}")
    
    logger.info(f"\n=== Final Summary ===")
    logger.info(f"  Notifications deleted: {deleted_count}")
    logger.info(f"  Client records deleted: {deleted_clients_count}")


if __name__ == "__main__":
    main()

