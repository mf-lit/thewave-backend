#!/usr/bin/env python3
"""Migration script to move data from DynamoDB to SQLite."""
import sys
from src.storage.dynamodb import DynamoDBStorage
from src.storage.sqlite import SQLiteStorage


def migrate_notifications(dynamo: DynamoDBStorage, sqlite: SQLiteStorage) -> int:
    """Migrate all notifications from DynamoDB to SQLite.

    Args:
        dynamo: DynamoDB storage instance
        sqlite: SQLite storage instance

    Returns:
        Number of notifications migrated
    """
    print("Fetching notifications from DynamoDB...")
    notifications = dynamo.get_all_notifications()
    print(f"Found {len(notifications)} notifications to migrate")

    if not notifications:
        return 0

    count = 0
    for notification in notifications:
        try:
            # Extract the fields needed for create_notification
            notification_data = {
                "performance_ak": notification["performance_ak"],
                "date": notification["date"],
                "time": notification["time"],
                "side": notification["side"],
                "title": notification["title"],
                "notification_type": notification["notification_type"],
                "last_checked_availability": notification.get("last_checked_availability"),
            }

            # Add thresholds if this is a below_threshold notification
            if notification["notification_type"] == "below_threshold":
                notification_data["thresholds"] = notification.get("thresholds", [])

            # Insert into SQLite with original IDs and timestamps
            cursor = sqlite.conn.cursor()

            # Handle thresholds and notified_thresholds as JSON
            import json
            thresholds_json = None
            notified_thresholds_json = None
            if notification["notification_type"] == "below_threshold":
                thresholds_json = json.dumps(notification.get("thresholds", []))
                notified_thresholds_json = json.dumps(notification.get("notified_thresholds", []))

            cursor.execute(
                """
                INSERT INTO notifications (
                    client_id, notification_id, performance_ak, date, time, side, title,
                    notification_type, thresholds, notified_thresholds,
                    last_checked_availability, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notification["client_id"],
                    notification["notification_id"],
                    notification["performance_ak"],
                    notification["date"],
                    notification["time"],
                    notification["side"],
                    notification["title"],
                    notification["notification_type"],
                    thresholds_json,
                    notified_thresholds_json,
                    notification.get("last_checked_availability"),
                    notification.get("created_at", ""),
                ),
            )
            sqlite.conn.commit()

            count += 1
            if count % 10 == 0:
                print(f"Migrated {count} notifications...")

        except Exception as e:
            print(f"Error migrating notification {notification.get('notification_id')}: {e}")
            continue

    return count


def migrate_clients(dynamo: DynamoDBStorage, sqlite: SQLiteStorage) -> int:
    """Migrate all client tokens from DynamoDB to SQLite.

    Args:
        dynamo: DynamoDB storage instance
        sqlite: SQLite storage instance

    Returns:
        Number of clients migrated
    """
    print("\nFetching clients from DynamoDB...")

    # Scan the clients table in DynamoDB
    try:
        dynamo.ensure_clients_table_exists()
        clients_table = dynamo.dynamodb.Table("waveform-clients")
        response = clients_table.scan()
        clients = response.get("Items", [])

        # Handle pagination
        while "LastEvaluatedKey" in response:
            response = clients_table.scan(ExclusiveStartKey=response["LastEvaluatedKey"])
            clients.extend(response.get("Items", []))

        print(f"Found {len(clients)} clients to migrate")

        if not clients:
            return 0

        count = 0
        for client in clients:
            try:
                cursor = sqlite.conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO clients (client_id, fcm_token, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (
                        client["client_id"],
                        client["fcm_token"],
                        client.get("updated_at", ""),
                    ),
                )
                sqlite.conn.commit()

                count += 1

            except Exception as e:
                print(f"Error migrating client {client.get('client_id')}: {e}")
                continue

        return count

    except Exception as e:
        print(f"Error fetching clients: {e}")
        return 0


def main():
    """Run the migration."""
    print("=== DynamoDB to SQLite Migration ===\n")

    # Initialize storage instances
    print("Connecting to DynamoDB...")
    dynamo = DynamoDBStorage()

    print("Connecting to SQLite...")
    sqlite = SQLiteStorage()

    # Ensure tables exist
    print("Ensuring SQLite tables exist...")
    sqlite.ensure_table_exists()
    sqlite.ensure_clients_table_exists()

    # Migrate notifications
    notification_count = migrate_notifications(dynamo, sqlite)
    print(f"\n✓ Migrated {notification_count} notifications")

    # Migrate clients
    client_count = migrate_clients(dynamo, sqlite)
    print(f"✓ Migrated {client_count} clients")

    # Close connections
    sqlite.close()

    print("\n=== Migration Complete ===")
    print(f"Total notifications: {notification_count}")
    print(f"Total clients: {client_count}")
    print(f"Database saved to: {sqlite.db_path}")


if __name__ == "__main__":
    main()
