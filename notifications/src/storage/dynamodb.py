"""DynamoDB storage layer for notifications."""
import os
import uuid
from decimal import Decimal
from datetime import datetime
from typing import Any, Dict, List, Optional

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


class DynamoDBStorage:
    """Handles all DynamoDB operations for notifications."""

    @staticmethod
    def _convert_decimals(obj: Any) -> Any:
        """Convert Decimal types to native Python types for JSON serialization.
        
        Args:
            obj: Object that may contain Decimal types
            
        Returns:
            Object with Decimal types converted to int/float
        """
        if isinstance(obj, Decimal):
            # Convert Decimal to int if it's a whole number, otherwise float
            if obj % 1 == 0:
                return int(obj)
            return float(obj)
        elif isinstance(obj, dict):
            return {k: DynamoDBStorage._convert_decimals(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [DynamoDBStorage._convert_decimals(item) for item in obj]
        return obj

    def __init__(self, table_name: str = "waveform-notifications"):
        """Initialize DynamoDB client and table.

        Args:
            table_name: Name of the DynamoDB table
        """
        endpoint_url = os.getenv("AWS_ENDPOINT_URL", "http://192.168.1.1:8001")
        self.dynamodb = boto3.resource(
            "dynamodb",
            endpoint_url=endpoint_url,
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        self.table_name = table_name
        self.table = None

    def ensure_table_exists(self):
        """Create the table if it doesn't exist."""
        try:
            self.table = self.dynamodb.Table(self.table_name)
            # Try to describe the table to check if it exists
            self.table.meta.client.describe_table(TableName=self.table_name)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                # Table doesn't exist, create it
                self.table = self.dynamodb.create_table(
                    TableName=self.table_name,
                    KeySchema=[
                        {"AttributeName": "client_id", "KeyType": "HASH"},
                        {"AttributeName": "notification_id", "KeyType": "RANGE"},
                    ],
                    AttributeDefinitions=[
                        {"AttributeName": "client_id", "AttributeType": "S"},
                        {"AttributeName": "notification_id", "AttributeType": "S"},
                        {"AttributeName": "performance_ak", "AttributeType": "S"},
                    ],
                    GlobalSecondaryIndexes=[
                        {
                            "IndexName": "performance_ak-index",
                            "KeySchema": [
                                {"AttributeName": "performance_ak", "KeyType": "HASH"}
                            ],
                            "Projection": {"ProjectionType": "ALL"},
                        }
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )
                # Wait for table to be created
                self.table.wait_until_exists()
            else:
                raise

    def create_notification(
        self, client_id: str, notification_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Create a new notification.

        Args:
            client_id: UUID of the client
            notification_data: Notification data (date, time, side, etc.)

        Returns:
            Created notification with notification_id
        """
        if self.table is None:
            self.ensure_table_exists()

        notification_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        item = {
            "client_id": client_id,
            "notification_id": notification_id,
            "performance_ak": notification_data["performance_ak"],
            "date": notification_data["date"],
            "time": notification_data["time"],
            "side": notification_data["side"],
            "title": notification_data["title"],
            "notification_type": notification_data["notification_type"],
            "last_checked_availability": notification_data.get(
                "last_checked_availability", None
            ),
            "created_at": now,
        }

        if notification_data["notification_type"] == "below_threshold":
            item["thresholds"] = notification_data["thresholds"]
            item["notified_thresholds"] = []  # Track which thresholds have been notified

        self.table.put_item(Item=item)
        return item

    def get_notifications_by_client(self, client_id: str) -> List[Dict[str, Any]]:
        """Get all notifications for a specific client.

        Args:
            client_id: UUID of the client

        Returns:
            List of notifications
        """
        if self.table is None:
            self.ensure_table_exists()

        response = self.table.query(
            KeyConditionExpression=Key("client_id").eq(client_id)
        )
        items = response.get("Items", [])
        # Convert Decimal types to native Python types
        return [self._convert_decimals(item) for item in items]

    def get_notification_by_id(
        self, client_id: str, notification_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get a specific notification by ID.

        Args:
            client_id: UUID of the client
            notification_id: UUID of the notification

        Returns:
            Notification if found, None otherwise
        """
        if self.table is None:
            self.ensure_table_exists()

        try:
            response = self.table.get_item(
                Key={"client_id": client_id, "notification_id": notification_id}
            )
            item = response.get("Item")
            if item:
                return self._convert_decimals(item)
            return None
        except ClientError:
            return None

    def delete_notification(self, client_id: str, notification_id: str) -> bool:
        """Delete a notification.

        Args:
            client_id: UUID of the client
            notification_id: UUID of the notification

        Returns:
            True if deleted, False if not found
        """
        if self.table is None:
            self.ensure_table_exists()

        try:
            self.table.delete_item(
                Key={"client_id": client_id, "notification_id": notification_id}
            )
            return True
        except ClientError:
            return False

    def update_notification(
        self, client_id: str, notification_id: str, updates: Dict[str, Any]
    ) -> bool:
        """Update a notification.

        Args:
            client_id: UUID of the client
            notification_id: UUID of the notification
            updates: Dictionary of fields to update

        Returns:
            True if updated, False if not found
        """
        if self.table is None:
            self.ensure_table_exists()

        # Build update expression
        update_expression_parts = []
        expression_attribute_names = {}
        expression_attribute_values = {}

        for key, value in updates.items():
            update_expression_parts.append(f"#{key} = :{key}")
            expression_attribute_names[f"#{key}"] = key
            expression_attribute_values[f":{key}"] = value

        if not update_expression_parts:
            return False

        update_expression = "SET " + ", ".join(update_expression_parts)

        try:
            self.table.update_item(
                Key={"client_id": client_id, "notification_id": notification_id},
                UpdateExpression=update_expression,
                ExpressionAttributeNames=expression_attribute_names,
                ExpressionAttributeValues=expression_attribute_values,
            )
            return True
        except ClientError:
            return False

    def get_all_notifications(self) -> List[Dict[str, Any]]:
        """Get all notifications across all clients (for daemon use).

        Returns:
            List of all notifications
        """
        if self.table is None:
            self.ensure_table_exists()

        notifications = []
        response = self.table.scan()
        notifications.extend(response.get("Items", []))

        # Handle pagination
        while "LastEvaluatedKey" in response:
            response = self.table.scan(
                ExclusiveStartKey=response["LastEvaluatedKey"]
            )
            notifications.extend(response.get("Items", []))

        # Convert Decimal types to native Python types
        return [self._convert_decimals(item) for item in notifications]

    def ensure_clients_table_exists(self, table_name: str = "waveform-clients"):
        """Create the clients table if it doesn't exist.

        Args:
            table_name: Name of the clients table
        """
        try:
            clients_table = self.dynamodb.Table(table_name)
            # Try to describe the table to check if it exists
            clients_table.meta.client.describe_table(TableName=table_name)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                # Table doesn't exist, create it
                clients_table = self.dynamodb.create_table(
                    TableName=table_name,
                    KeySchema=[{"AttributeName": "client_id", "KeyType": "HASH"}],
                    AttributeDefinitions=[
                        {"AttributeName": "client_id", "AttributeType": "S"}
                    ],
                    BillingMode="PAY_PER_REQUEST",
                )
                # Wait for table to be created
                clients_table.wait_until_exists()

    def create_or_update_client_token(
        self, client_id: str, fcm_token: str, table_name: str = "waveform-clients"
    ) -> Dict[str, Any]:
        """Create or update FCM token for a client.

        Args:
            client_id: UUID of the client
            fcm_token: Firebase Cloud Messaging token
            table_name: Name of the clients table

        Returns:
            Client record with fcm_token and updated_at
        """
        self.ensure_clients_table_exists(table_name)
        clients_table = self.dynamodb.Table(table_name)

        now = datetime.utcnow().isoformat()

        item = {
            "client_id": client_id,
            "fcm_token": fcm_token,
            "updated_at": now,
        }

        clients_table.put_item(Item=item)
        return item

    def get_client_token(
        self, client_id: str, table_name: str = "waveform-clients"
    ) -> Optional[str]:
        """Get FCM token for a client.

        Args:
            client_id: UUID of the client
            table_name: Name of the clients table

        Returns:
            FCM token string if found, None otherwise
        """
        self.ensure_clients_table_exists(table_name)
        clients_table = self.dynamodb.Table(table_name)

        try:
            response = clients_table.get_item(Key={"client_id": client_id})
            item = response.get("Item")
            if item:
                return item.get("fcm_token")
            return None
        except ClientError:
            return None

    def delete_client_token(
        self, client_id: str, table_name: str = "waveform-clients"
    ) -> bool:
        """Delete FCM token for a client.

        Args:
            client_id: UUID of the client
            table_name: Name of the clients table

        Returns:
            True if deleted, False if not found
        """
        self.ensure_clients_table_exists(table_name)
        clients_table = self.dynamodb.Table(table_name)

        try:
            clients_table.delete_item(Key={"client_id": client_id})
            return True
        except ClientError:
            return False

    def get_notifications_by_performance_ak(
        self, performance_ak: str
    ) -> List[Dict[str, Any]]:
        """Get all notifications for a specific performance across all clients.

        Args:
            performance_ak: Performance identifier

        Returns:
            List of notifications
        """
        if self.table is None:
            self.ensure_table_exists()

        index_name = "performance_ak-index"

        response = self.table.query(
            IndexName=index_name,
            KeyConditionExpression=Key("performance_ak").eq(performance_ak),
        )
        notifications = response.get("Items", [])

        # Handle pagination
        while "LastEvaluatedKey" in response:
            response = self.table.query(
                IndexName=index_name,
                KeyConditionExpression=Key("performance_ak").eq(performance_ak),
                ExclusiveStartKey=response["LastEvaluatedKey"],
            )
            notifications.extend(response.get("Items", []))

        # Convert Decimal types to native Python types
        return [self._convert_decimals(item) for item in notifications]

