"""API routes for notifications."""
import json
import logging
import uuid
from typing import Tuple

from flask import Blueprint, jsonify, request

from src.api.models import CreateNotificationRequest, NotificationResponse
from src.storage.sqlite import SQLiteStorage
from src.utils.calendar import (
    extract_availability_by_side,
    fetch_calendar_data,
    find_performance_by_ak,
    get_performance_title,
)
from src.utils.fcm import validate_fcm_token

logger = logging.getLogger(__name__)

bp = Blueprint("notifications", __name__)
storage = SQLiteStorage()


def log_response(endpoint: str, response_data: dict, status_code: int) -> None:
    """Log response data for debugging."""
    logger.info(f"{endpoint} - Response ({status_code}): {json.dumps(response_data, indent=2)}")


def is_valid_uuid(value: str) -> bool:
    """Check if a string is a valid UUID."""
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def error_response(message: str, status_code: int, endpoint: str) -> Tuple:
    """Create a standardized error response with logging."""
    response_data = {"error": message}
    log_response(endpoint, response_data, status_code)
    return jsonify(response_data), status_code


def success_response(data: dict, status_code: int, endpoint: str) -> Tuple:
    """Create a standardized success response with logging."""
    log_response(endpoint, data, status_code)
    return jsonify(data), status_code


@bp.route("/clients/<client_id>/notifications", methods=["POST"])
def create_notification(client_id: str):
    """Create a new notification for a client."""
    endpoint = f"POST /clients/{client_id}/notifications"
    data = request.get_json()
    logger.info(f"{endpoint} - Request body: {json.dumps(data, indent=2)}")

    if not is_valid_uuid(client_id):
        return error_response("Invalid client_id format", 400, endpoint)

    if not data:
        return error_response("Request body is required", 400, endpoint)

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
        return error_response(f"Missing required field: {e.args[0]}", 400, endpoint)

    is_valid, validation_error = req.validate()
    if not is_valid:
        return error_response(validation_error, 400, endpoint)

    # Fetch calendar data to validate performance exists
    try:
        calendar_data = fetch_calendar_data(req.date, 1)
        performance = find_performance_by_ak(calendar_data, req.performance_ak)
        if not performance:
            return error_response(f"Performance not found for performanceAK={req.performance_ak}", 404, endpoint)
    except Exception as e:
        return error_response(f"Failed to fetch calendar data: {str(e)}", 500, endpoint)

    # Verify the side exists for this performance
    availability = extract_availability_by_side(performance, req.side)
    if availability is None:
        return error_response(f"Side '{req.side}' not found for performance {req.performance_ak}", 400, endpoint)

    notification_data = {
        "performance_ak": req.performance_ak,
        "date": req.date,
        "time": req.time,
        "side": req.side,
        "title": get_performance_title(performance),
        "notification_type": req.notification_type,
    }
    if req.thresholds:
        notification_data["thresholds"] = req.thresholds

    try:
        storage.ensure_table_exists()
        notification = storage.create_notification(client_id, notification_data)
        response_data = NotificationResponse.from_dict(notification).to_dict()
        return success_response(response_data, 201, endpoint)
    except Exception as e:
        return error_response(f"Failed to create notification: {str(e)}", 500, endpoint)


@bp.route("/clients/<client_id>/notifications", methods=["GET"])
def list_notifications(client_id: str):
    """List all notifications for a client."""
    endpoint = f"GET /clients/{client_id}/notifications"
    logger.info(f"{endpoint} - Request")

    if not is_valid_uuid(client_id):
        return error_response("Invalid client_id format", 400, endpoint)

    try:
        storage.ensure_table_exists()
        notifications = storage.get_notifications_by_client(client_id)
        response_data = [NotificationResponse.from_dict(n).to_dict() for n in notifications]
        return success_response(response_data, 200, endpoint)
    except Exception as e:
        return error_response(f"Failed to list notifications: {str(e)}", 500, endpoint)


@bp.route("/clients/<client_id>/notifications/<notification_id>", methods=["DELETE"])
def delete_notification(client_id: str, notification_id: str):
    """Delete a notification."""
    endpoint = f"DELETE /clients/{client_id}/notifications/{notification_id}"
    logger.info(f"{endpoint} - Request")

    if not is_valid_uuid(client_id):
        return error_response("Invalid client_id format", 400, endpoint)
    if not is_valid_uuid(notification_id):
        return error_response("Invalid notification_id format", 400, endpoint)

    try:
        storage.ensure_table_exists()
        notification = storage.get_notification_by_id(client_id, notification_id)
        if not notification:
            return error_response("Notification not found", 404, endpoint)

        deleted = storage.delete_notification(client_id, notification_id)
        if deleted:
            return success_response({"message": "Notification deleted"}, 200, endpoint)
        return error_response("Failed to delete notification", 500, endpoint)
    except Exception as e:
        return error_response(f"Failed to delete notification: {str(e)}", 500, endpoint)


@bp.route("/clients/<client_id>/fcm-token", methods=["PUT"])
def create_or_update_fcm_token(client_id: str):
    """Create or update FCM token for a client."""
    endpoint = f"PUT /clients/{client_id}/fcm-token"
    data = request.get_json()
    logger.info(f"{endpoint} - Request body: {json.dumps(data, indent=2) if data else 'None'}")

    if not is_valid_uuid(client_id):
        return error_response("Invalid client_id format", 400, endpoint)

    if not data:
        return error_response("Request body is required", 400, endpoint)

    fcm_token = data.get("fcm_token")
    if not fcm_token:
        return error_response("fcm_token is required", 400, endpoint)

    is_valid, validation_error = validate_fcm_token(fcm_token)
    if not is_valid:
        return error_response(validation_error, 400, endpoint)

    try:
        storage.ensure_clients_table_exists()
        client_record = storage.create_or_update_client_token(client_id, fcm_token)
        response_data = {
            "message": "FCM token saved successfully",
            "updated_at": client_record["updated_at"],
        }
        return success_response(response_data, 200, endpoint)
    except Exception as e:
        return error_response(f"Failed to save FCM token: {str(e)}", 500, endpoint)


@bp.route("/clients/<client_id>/fcm-token", methods=["GET"])
def get_fcm_token(client_id: str):
    """Check if FCM token exists for a client (does not return actual token for security)."""
    endpoint = f"GET /clients/{client_id}/fcm-token"
    logger.info(f"{endpoint} - Request")

    if not is_valid_uuid(client_id):
        return error_response("Invalid client_id format", 400, endpoint)

    try:
        storage.ensure_clients_table_exists()
        fcm_token = storage.get_client_token(client_id)
        if fcm_token:
            return success_response({"has_token": True}, 200, endpoint)
        return error_response("FCM token not found", 404, endpoint)
    except Exception as e:
        return error_response(f"Failed to get FCM token: {str(e)}", 500, endpoint)


@bp.route("/clients/<client_id>/fcm-token", methods=["DELETE"])
def delete_fcm_token(client_id: str):
    """Delete FCM token for a client."""
    endpoint = f"DELETE /clients/{client_id}/fcm-token"
    logger.info(f"{endpoint} - Request")

    if not is_valid_uuid(client_id):
        return error_response("Invalid client_id format", 400, endpoint)

    try:
        storage.ensure_clients_table_exists()
        deleted = storage.delete_client_token(client_id)
        if deleted:
            return success_response({"message": "FCM token deleted successfully"}, 200, endpoint)
        return error_response("FCM token not found", 404, endpoint)
    except Exception as e:
        return error_response(f"Failed to delete FCM token: {str(e)}", 500, endpoint)

