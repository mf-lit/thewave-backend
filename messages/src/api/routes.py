"""HTTP endpoints.

Handlers stay thin: authenticate, validate, call a repository, serialise. The
status codes and message strings here are the published contract — see
tests/test_api_contract.py, which pins every one of them.

Two blueprints. ``bp`` is what the app and the webapp call, reachable from the
internet through the tunnel and guarded at the edge by a path allow-list.
``admin_bp`` is not: the edge rules do not admit ``/admin/*`` at all, so it is
served only over the Docker network, and ``app.py`` declines to register it
when no admin keys are configured.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Tuple

from flask import Blueprint, current_app, jsonify, request

from .. import markdown
from ..clock import utc_now
from ..models import MAX_BODY_LENGTH, AudienceRequest, MessageRequest, read_flag
from ..targeting import Client, audience_matches, select
from ..validation import ValidationError
from .auth import require_admin_key, require_api_key
from .handlers import ApiError

logger = logging.getLogger(__name__)

bp = Blueprint("messages", __name__)
admin_bp = Blueprint("admin", __name__, url_prefix="/admin")

CLIENT_ID_HEADER = "X-Client-ID"
CLIENT_OS_HEADER = "X-Client-OS"
CLIENT_VERSION_HEADER = "X-Client-Version"

MISSING_CLIENT_ID_ERROR = f"Missing {CLIENT_ID_HEADER} header"
ACKS_FORMAT_ERROR = "Invalid acks. Expected a list of {message_id, revision} objects"
NOT_FOUND_ERROR = "Message not found"

# How many client IDs a dry-run audience count returns alongside the number.
# Enough to eyeball against sqlite-web, far short of exporting the user list.
AUDIENCE_SAMPLE_SIZE = 10


def services():
    return current_app.extensions["messages"]


def _uuid_or_400(value: Any, field: str) -> str:
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError):
        raise ValidationError(f"Invalid {field} format")
    return value


def _json_body() -> dict:
    """The request body, or a 400 if it is empty.

    ``get_json()`` is deliberately not silent: a request without a JSON
    content type produces Werkzeug's 415, which is the same thing the other
    services do and worth keeping uniform.
    """
    body = request.get_json()
    if not body:
        raise ValidationError("Request body is required")
    return body


def _caller() -> Client:
    """The requesting client, from its headers and the upstream directory.

    ``X-Client-ID`` is required — everything here is per-client, and a request
    without one has no answer rather than a default one. The other two are
    optional and fail closed only against a message that targets on them.
    """
    client_id = request.headers.get(CLIENT_ID_HEADER)
    if not client_id:
        raise ValidationError(MISSING_CLIENT_ID_ERROR)
    _uuid_or_400(client_id, CLIENT_ID_HEADER)

    return Client.build(
        client_id=client_id,
        client_os=request.headers.get(CLIENT_OS_HEADER),
        client_version=request.headers.get(CLIENT_VERSION_HEADER),
        days_count=services().directory.lookup(client_id),
    )


def _parse_acks(body: dict) -> List[Tuple[str, int]]:
    acks = body.get("acks")
    if not isinstance(acks, list):
        raise ValidationError(ACKS_FORMAT_ERROR)

    parsed: List[Tuple[str, int]] = []
    for ack in acks:
        if not isinstance(ack, dict):
            raise ValidationError(ACKS_FORMAT_ERROR)
        message_id = ack.get("message_id")
        revision = ack.get("revision")
        if not isinstance(message_id, str) or not message_id:
            raise ValidationError(ACKS_FORMAT_ERROR)
        if not isinstance(revision, int) or isinstance(revision, bool):
            raise ValidationError(ACKS_FORMAT_ERROR)
        parsed.append((message_id, revision))
    return parsed


# ---------------------------------------------------------------- client API


@bp.route("/messages")
@require_api_key
def list_messages():
    app = services()
    client = _caller()

    selected = select(
        app.messages.list_enabled(),
        client,
        app.messages.acked_revisions(client.client_id),
        utc_now(),
    )

    response = jsonify(
        {
            "messages": [message.to_api() for message in selected],
            "ttl": app.settings.ttl_seconds,
        }
    )
    # Belt and braces alongside the edge and nginx rules: this response is
    # per-client, and anything that caches it serves one user's targeted
    # messages to another.
    response.headers["Cache-Control"] = "no-store"
    return response, 200


@bp.route("/messages/acks", methods=["POST"])
@require_api_key
def record_acks():
    body = _json_body()
    client_id = _uuid_or_400(body.get("client_id"), "client_id")
    acks = _parse_acks(body)

    recorded = services().messages.record_acks(client_id, acks)
    return jsonify({"recorded": recorded}), 200


@bp.route("/health")
def health():
    """Unauthenticated, so the container healthcheck needs no key."""
    return jsonify({"status": "ok"}), 200


# ----------------------------------------------------------------- admin API


def _message_or_404(message_id: str):
    message = services().messages.get(message_id)
    if message is None:
        raise ApiError(NOT_FOUND_ERROR, 404)
    return message


@admin_bp.route("/messages", methods=["GET"])
@require_admin_key
def admin_list_messages():
    app = services()
    counts = app.messages.ack_counts()
    # Read once for the whole listing rather than per message: the audience is
    # the same set of clients for every row.
    clients = app.directory.iter_clients()

    items: List[Dict[str, Any]] = []
    for message in app.messages.list_all():
        item = message.to_admin_api()
        item["ack_count"] = counts.get(message.message_id, 0)
        item["matched_client_count"] = sum(
            1 for client in clients if audience_matches(message, client)
        )
        items.append(item)

    return jsonify({"messages": items}), 200


@admin_bp.route("/messages", methods=["POST"])
@require_admin_key
def admin_create_message():
    message = services().messages.create(MessageRequest.from_payload(_json_body()))
    return jsonify(message.to_admin_api()), 201


@admin_bp.route("/messages/<message_id>", methods=["GET"])
@require_admin_key
def admin_get_message(message_id: str):
    return jsonify(_message_or_404(message_id).to_admin_api()), 200


@admin_bp.route("/messages/<message_id>", methods=["PUT"])
@require_admin_key
def admin_update_message(message_id: str):
    body = _json_body()
    # Not a message field, so it is read before validation and never stored:
    # whether an edit is substantive enough to re-show is a judgement about
    # the edit, and only the person making it knows.
    bump_revision = read_flag(body, "bump_revision", False)
    request_model = MessageRequest.from_payload(body)

    message = services().messages.update(message_id, request_model, bump_revision)
    if message is None:
        raise ApiError(NOT_FOUND_ERROR, 404)
    return jsonify(message.to_admin_api()), 200


@admin_bp.route("/messages/<message_id>", methods=["DELETE"])
@require_admin_key
def admin_delete_message(message_id: str):
    if not services().messages.delete(message_id):
        raise ApiError(NOT_FOUND_ERROR, 404)
    return jsonify({"message": "Message deleted"}), 200


@admin_bp.route("/messages/<message_id>/enabled", methods=["POST"])
@require_admin_key
def admin_set_enabled(message_id: str):
    body = _json_body()
    if "enabled" not in body:
        raise ValidationError("Missing required field: enabled")
    enabled = read_flag(body, "enabled", True)

    message = services().messages.set_enabled(message_id, enabled)
    if message is None:
        raise ApiError(NOT_FOUND_ERROR, 404)
    return jsonify(message.to_admin_api()), 200


@admin_bp.route("/preview", methods=["POST"])
@require_admin_key
def admin_preview():
    """Parse a draft body and hand back the tree, or the error it would fail on.

    The dashboard's live preview renders this rather than re-parsing in
    JavaScript: a second implementation of the grammar is a second thing that
    can disagree with the one that decides whether a save succeeds.
    """
    body = _json_body().get("body")
    if not isinstance(body, str):
        raise ValidationError(markdown.BODY_NOT_TEXT_ERROR)
    # The preview is not a save, but it should not accept what a save would
    # reject — an operator finding out at the last keystroke is the failure
    # this endpoint exists to prevent.
    if len(body.strip()) > MAX_BODY_LENGTH:
        raise ValidationError(f"body must be at most {MAX_BODY_LENGTH} characters")

    return jsonify({"blocks": markdown.to_json(markdown.parse(body))}), 200


@admin_bp.route("/audience", methods=["POST"])
@require_admin_key
def admin_audience():
    """Dry-run a draft's targeting rules against the current client base.

    Answers "who would this reach", so it reads neither the window nor the
    acks: a message that has not been written yet has no start time, and
    nobody has acked it.
    """
    rules = AudienceRequest.from_payload(_json_body())

    matched = [
        client.client_id
        for client in services().directory.iter_clients()
        if audience_matches(rules, client)
    ]

    return (
        jsonify({"count": len(matched), "sample": matched[:AUDIENCE_SAMPLE_SIZE]}),
        200,
    )
