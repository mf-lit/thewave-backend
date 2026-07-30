"""HTTP endpoints.

Handlers stay thin: authenticate, validate, call a repository, serialise.
The status codes and message strings here are the published contract — see
tests/test_api_contract.py, which pins every one of them.
"""
from __future__ import annotations

import logging
import uuid

from flask import Blueprint, current_app, jsonify, request

from ..calendar_client import CalendarError, availability_for_side, performance_title
from ..fcm_token import validate_fcm_token
from ..models import NotificationRequest, ValidationError
from .auth import require_api_key
from .handlers import ApiError

logger = logging.getLogger(__name__)

bp = Blueprint("notifications", __name__)


def services():
    return current_app.extensions["notifications"]


def _uuid_or_400(value: str, field: str) -> str:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ValidationError(f"Invalid {field} format")
    return value


def _json_body() -> dict:
    """The request body, or a 400 if it is empty.

    ``get_json()`` is deliberately not silent: a request without a JSON
    content type has always produced Werkzeug's 415, and clients in the wild
    depend on nothing more than that being stable.
    """
    body = request.get_json()
    if not body:
        raise ValidationError("Request body is required")
    return body


@bp.route("/clients/<client_id>/notifications", methods=["POST"])
@require_api_key
def create_notification(client_id: str):
    _uuid_or_400(client_id, "client_id")
    payload = _json_body()
    notification_request = NotificationRequest.from_payload(payload)

    app = services()
    try:
        calendar = app.calendar.fetch_day(notification_request.date)
    except CalendarError as exc:
        logger.error("Calendar lookup failed while creating notification: %s", exc)
        raise ApiError("Failed to fetch calendar data", 500)

    performance = calendar.find_performance(notification_request.performance_ak)
    if performance is None:
        raise ApiError(
            f"Performance not found for performanceAK={notification_request.performance_ak}", 404
        )

    if availability_for_side(performance, notification_request.side) is None:
        raise ApiError(
            f"Side '{notification_request.side}' not found for performance "
            f"{notification_request.performance_ak}",
            400,
        )

    notification = app.notifications.create(
        client_id, notification_request, performance_title(performance)
    )
    logger.info(
        "Created notification %s for client %s (%s %s %s, %s)",
        notification.notification_id,
        client_id,
        notification.date,
        notification.time,
        notification.side,
        notification.notification_type,
    )
    return jsonify(notification.to_api()), 201


@bp.route("/clients/<client_id>/notifications", methods=["GET"])
@require_api_key
def list_notifications(client_id: str):
    _uuid_or_400(client_id, "client_id")
    notifications = services().notifications.list_for_client(client_id)
    return jsonify([n.to_api() for n in notifications]), 200


@bp.route("/clients/<client_id>/notifications/<notification_id>", methods=["DELETE"])
@require_api_key
def delete_notification(client_id: str, notification_id: str):
    _uuid_or_400(client_id, "client_id")
    _uuid_or_400(notification_id, "notification_id")

    repository = services().notifications
    if repository.get(client_id, notification_id) is None:
        raise ApiError("Notification not found", 404)

    if not repository.delete(client_id, notification_id):
        raise ApiError("Failed to delete notification", 500)

    logger.info("Deleted notification %s for client %s", notification_id, client_id)
    return jsonify({"message": "Notification deleted"}), 200


@bp.route("/clients/<client_id>/fcm-token", methods=["PUT"])
@require_api_key
def put_fcm_token(client_id: str):
    _uuid_or_400(client_id, "client_id")
    body = _json_body()

    token = body.get("fcm_token")
    if not token:
        raise ValidationError("fcm_token is required")
    validate_fcm_token(token)

    updated_at = services().clients.upsert_token(client_id, token)
    logger.info("Stored FCM token for client %s", client_id)
    return jsonify({"message": "FCM token saved successfully", "updated_at": updated_at}), 200


@bp.route("/clients/<client_id>/fcm-token", methods=["GET"])
@require_api_key
def get_fcm_token(client_id: str):
    """Reports only whether a token exists; the value is never returned."""
    _uuid_or_400(client_id, "client_id")
    if services().clients.get_token(client_id) is None:
        raise ApiError("FCM token not found", 404)
    return jsonify({"has_token": True}), 200


@bp.route("/clients/<client_id>/fcm-token", methods=["DELETE"])
@require_api_key
def delete_fcm_token(client_id: str):
    _uuid_or_400(client_id, "client_id")
    if not services().clients.delete_token(client_id):
        raise ApiError("FCM token not found", 404)
    logger.info("Deleted FCM token for client %s", client_id)
    return jsonify({"message": "FCM token deleted successfully"}), 200


@bp.route("/health")
def health():
    return jsonify({"status": "ok"}), 200
