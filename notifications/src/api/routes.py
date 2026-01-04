"""API routes for notifications."""
import json
import logging
import uuid
from typing import Dict

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

from src.api.models import CreateNotificationRequest, NotificationResponse
from src.storage.dynamodb import DynamoDBStorage
from src.utils.calendar import (
    extract_availability_by_side,
    fetch_calendar_data,
    find_performance_by_ak,
    get_performance_title,
)

bp = Blueprint("notifications", __name__)
storage = DynamoDBStorage()


def log_response(endpoint: str, response_data: dict, status_code: int):
    """Helper function to log response data."""
    logger.info(
        f"{endpoint} - Response ({status_code}): {json.dumps(response_data, indent=2)}"
    )


def validate_uuid(uuid_string: str) -> bool:
    """Validate that a string is a valid UUID."""
    try:
        uuid.UUID(uuid_string)
        return True
    except (ValueError, AttributeError):
        return False


@bp.route("/clients/<client_id>/notifications", methods=["POST"])
def create_notification(client_id: str):
    """Create a new notification for a client."""
    # Log request
    data = request.get_json()
    logger.info(
        f"POST /clients/{client_id}/notifications - Request body: {json.dumps(data, indent=2)}"
    )

    # Validate client_id
    if not validate_uuid(client_id):
        response_data = {"error": "Invalid client_id format"}
        log_response(f"POST /clients/{client_id}/notifications", response_data, 400)
        return jsonify(response_data), 400

    # Parse and validate request body
    if not data:
        response_data = {"error": "Request body is required"}
        log_response(f"POST /clients/{client_id}/notifications", response_data, 400)
        return jsonify(response_data), 400

    try:
        req = CreateNotificationRequest(
            performance_ak=data["performance_ak"],
            date=data["date"],
            time=data["time"],
            side=data["side"],
            notification_type=data["notification_type"],
            thresholds=data.get("thresholds"),
        )
    except KeyError as e:
        response_data = {"error": f"Missing required field: {e.args[0]}"}
        log_response(f"POST /clients/{client_id}/notifications", response_data, 400)
        return jsonify(response_data), 400

    # Validate request
    is_valid, error_msg = req.validate()
    if not is_valid:
        response_data = {"error": error_msg}
        log_response(f"POST /clients/{client_id}/notifications", response_data, 400)
        return jsonify(response_data), 400

    # Fetch calendar data to validate performanceAK exists and get title
    try:
        calendar_data = fetch_calendar_data(req.date, 1)
        performance = find_performance_by_ak(calendar_data, req.performance_ak)
        if not performance:
            response_data = {
                "error": f"Performance not found for performanceAK={req.performance_ak}"
            }
            log_response(f"POST /clients/{client_id}/notifications", response_data, 404)
            return jsonify(response_data), 404
    except Exception as e:
        response_data = {"error": f"Failed to fetch calendar data: {str(e)}"}
        log_response(f"POST /clients/{client_id}/notifications", response_data, 500)
        return jsonify(response_data), 500

    # Verify the side exists for this performance
    availability = extract_availability_by_side(performance, req.side)
    if availability is None:
        response_data = {
            "error": f"Side '{req.side}' not found for performance {req.performance_ak}"
        }
        log_response(f"POST /clients/{client_id}/notifications", response_data, 400)
        return jsonify(response_data), 400

    title = get_performance_title(performance)

    # Create notification in DynamoDB
    notification_data = {
        "performance_ak": req.performance_ak,
        "date": req.date,
        "time": req.time,
        "side": req.side,
        "title": title,
        "notification_type": req.notification_type,
    }
    if req.thresholds:
        notification_data["thresholds"] = req.thresholds

    try:
        storage.ensure_table_exists()
        notification = storage.create_notification(client_id, notification_data)
        response = NotificationResponse.from_dict(notification)
        response_data = response.to_dict()
        log_response(f"POST /clients/{client_id}/notifications", response_data, 201)
        return jsonify(response_data), 201
    except Exception as e:
        response_data = {"error": f"Failed to create notification: {str(e)}"}
        log_response(f"POST /clients/{client_id}/notifications", response_data, 500)
        return jsonify(response_data), 500


@bp.route("/clients/<client_id>/notifications", methods=["GET"])
def list_notifications(client_id: str):
    """List all notifications for a client."""
    logger.info(f"GET /clients/{client_id}/notifications - Request")
    
    # Validate client_id
    if not validate_uuid(client_id):
        response_data = {"error": "Invalid client_id format"}
        log_response(f"GET /clients/{client_id}/notifications", response_data, 400)
        return jsonify(response_data), 400

    try:
        storage.ensure_table_exists()
        notifications = storage.get_notifications_by_client(client_id)
        response_data = [
            NotificationResponse.from_dict(n).to_dict() for n in notifications
        ]
        log_response(f"GET /clients/{client_id}/notifications", response_data, 200)
        return jsonify(response_data), 200
    except Exception as e:
        response_data = {"error": f"Failed to list notifications: {str(e)}"}
        log_response(f"GET /clients/{client_id}/notifications", response_data, 500)
        return jsonify(response_data), 500


@bp.route(
    "/clients/<client_id>/notifications/<notification_id>", methods=["DELETE"]
)
def delete_notification(client_id: str, notification_id: str):
    """Delete a notification."""
    logger.info(f"DELETE /clients/{client_id}/notifications/{notification_id} - Request")
    
    # Validate UUIDs
    if not validate_uuid(client_id):
        response_data = {"error": "Invalid client_id format"}
        log_response(f"DELETE /clients/{client_id}/notifications/{notification_id}", response_data, 400)
        return jsonify(response_data), 400
    if not validate_uuid(notification_id):
        response_data = {"error": "Invalid notification_id format"}
        log_response(f"DELETE /clients/{client_id}/notifications/{notification_id}", response_data, 400)
        return jsonify(response_data), 400

    try:
        storage.ensure_table_exists()
        # Verify the notification belongs to the client
        notification = storage.get_notification_by_id(client_id, notification_id)
        if not notification:
            response_data = {"error": "Notification not found"}
            log_response(f"DELETE /clients/{client_id}/notifications/{notification_id}", response_data, 404)
            return jsonify(response_data), 404

        # Delete the notification
        deleted = storage.delete_notification(client_id, notification_id)
        if deleted:
            response_data = {"message": "Notification deleted"}
            log_response(f"DELETE /clients/{client_id}/notifications/{notification_id}", response_data, 200)
            return jsonify(response_data), 200
        else:
            response_data = {"error": "Failed to delete notification"}
            log_response(f"DELETE /clients/{client_id}/notifications/{notification_id}", response_data, 500)
            return jsonify(response_data), 500
    except Exception as e:
        response_data = {"error": f"Failed to delete notification: {str(e)}"}
        log_response(f"DELETE /clients/{client_id}/notifications/{notification_id}", response_data, 500)
        return jsonify(response_data), 500

