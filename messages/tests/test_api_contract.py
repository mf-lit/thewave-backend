"""Every status code and error string, pinned verbatim.

Two of these are load-bearing beyond their own assertion: a client-targeted
message must be invisible to a different ``X-Client-ID``, and ``/admin/*``
must not exist at all when no admin keys are configured.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import yaml

from src.api.app import admin_enabled, create_app
from src.api.auth import (
    INVALID_ADMIN_KEY_ERROR,
    INVALID_KEY_ERROR,
    MISSING_ADMIN_KEY_ERROR,
    MISSING_KEY_ERROR,
)
from src.api.routes import ACKS_FORMAT_ERROR, MISSING_CLIENT_ID_ERROR, NOT_FOUND_ERROR
from src.models import MessageRequest
from src.services import Services
from src.settings import Settings

from conftest import ADMIN_KEY, API_KEY, build_upstream_db, make_payload

CLIENT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ID = "22222222-2222-2222-2222-222222222222"


def headers(client_id=CLIENT_ID, client_os="ios", client_version="1.2.3"):
    sent = {"x-api-key": API_KEY}
    if client_id is not None:
        sent["X-Client-ID"] = client_id
    if client_os is not None:
        sent["X-Client-OS"] = client_os
    if client_version is not None:
        sent["X-Client-Version"] = client_version
    return sent


def seed(services, **overrides):
    return services.messages.create(
        MessageRequest.from_payload(make_payload(**overrides))
    )


def error(response) -> str:
    return response.get_json()["error"]


# --------------------------------------------------------------------- health


def test_health_needs_no_key(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


# ----------------------------------------------------------------- client API


def test_get_messages_requires_an_api_key(client):
    response = client.get("/messages", headers={"X-Client-ID": CLIENT_ID})
    assert response.status_code == 401
    assert error(response) == MISSING_KEY_ERROR

    response = client.get(
        "/messages", headers={"x-api-key": "wrong", "X-Client-ID": CLIENT_ID}
    )
    assert response.status_code == 401
    assert error(response) == INVALID_KEY_ERROR


def test_get_messages_requires_a_client_id(client):
    response = client.get("/messages", headers={"x-api-key": API_KEY})
    assert response.status_code == 400
    assert error(response) == MISSING_CLIENT_ID_ERROR

    response = client.get("/messages", headers=headers(client_id="not-a-uuid"))
    assert response.status_code == 400
    assert error(response) == "Invalid X-Client-ID format"


def test_get_messages_serves_the_client_shape_and_a_ttl(client, services):
    seed(services, title="Closure", action_url="https://thewave.com", action_label="Book")

    response = client.get("/messages", headers=headers())
    assert response.status_code == 200

    body = response.get_json()
    assert body["ttl"] == services.settings.ttl_seconds
    assert len(body["messages"]) == 1

    message = body["messages"][0]
    assert set(message) == {
        "message_id",
        "revision",
        "title",
        "body",
        "display",
        "level",
        "retain",
        "expires_at",
        "action_url",
        "action_label",
        "dismissable_at",
        "dismissable_after",
    }
    assert message["title"] == "Closure"


def test_get_messages_is_never_cacheable(client):
    """A cached response here serves one user's targeted messages to another."""
    response = client.get("/messages", headers=headers())
    assert response.headers["Cache-Control"] == "no-store"


def test_a_client_targeted_message_is_invisible_to_another_client(client, services):
    seed(services, title="Just for you", client_ids=[CLIENT_ID])

    mine = client.get("/messages", headers=headers(client_id=CLIENT_ID)).get_json()
    theirs = client.get("/messages", headers=headers(client_id=OTHER_ID)).get_json()

    assert [m["title"] for m in mine["messages"]] == ["Just for you"]
    assert theirs["messages"] == []


def test_a_disabled_message_is_served_to_nobody(client, services):
    seed(services, enabled=False)
    assert client.get("/messages", headers=headers()).get_json()["messages"] == []


def test_os_targeting_uses_the_header(client, services):
    seed(services, title="Android only", os=["android"])

    assert client.get("/messages", headers=headers(client_os="ios")).get_json()[
        "messages"
    ] == []
    android = client.get("/messages", headers=headers(client_os="android")).get_json()
    assert [m["title"] for m in android["messages"]] == ["Android only"]


def test_days_count_comes_from_the_upstream_directory(tmp_path: Path, config_path: Path):
    """The one axis this service does not own, read from upstream-api's file."""
    upstream = build_upstream_db(
        tmp_path / "water_temperature.db",
        [{"uuid": CLIENT_ID, "days_count": 40}, {"uuid": OTHER_ID, "days_count": 1}],
    )
    settings = Settings(
        db_path=tmp_path / "messages.db",
        config_path=config_path,
        upstream_db_path=upstream,
        auth_disabled=False,
        ttl_seconds=900,
    )
    services = Services.build(settings=settings)
    seed(services, title="Welcome", max_days_count=1)

    app = create_app(services=services)
    app.config.update(TESTING=True)
    http = app.test_client()

    assert http.get("/messages", headers=headers(client_id=CLIENT_ID)).get_json()[
        "messages"
    ] == []
    fresh = http.get("/messages", headers=headers(client_id=OTHER_ID)).get_json()
    assert [m["title"] for m in fresh["messages"]] == ["Welcome"]


def test_post_acks_suppresses_the_message(client, services):
    message = seed(services)

    response = client.post(
        "/messages/acks",
        json={
            "client_id": CLIENT_ID,
            "acks": [{"message_id": message.message_id, "revision": 1}],
        },
        headers={"x-api-key": API_KEY},
    )
    assert response.status_code == 200
    assert response.get_json() == {"recorded": 1}

    assert client.get("/messages", headers=headers()).get_json()["messages"] == []


def test_post_acks_ignores_an_unknown_message(client, services):
    response = client.post(
        "/messages/acks",
        json={"client_id": CLIENT_ID, "acks": [{"message_id": "gone", "revision": 1}]},
        headers={"x-api-key": API_KEY},
    )
    assert response.status_code == 200
    assert response.get_json() == {"recorded": 0}


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"acks": []}, "Invalid client_id format"),
        ({"client_id": "nope", "acks": []}, "Invalid client_id format"),
        ({"client_id": CLIENT_ID}, ACKS_FORMAT_ERROR),
        ({"client_id": CLIENT_ID, "acks": "all"}, ACKS_FORMAT_ERROR),
        ({"client_id": CLIENT_ID, "acks": ["m1"]}, ACKS_FORMAT_ERROR),
        ({"client_id": CLIENT_ID, "acks": [{"message_id": "m1"}]}, ACKS_FORMAT_ERROR),
        (
            {"client_id": CLIENT_ID, "acks": [{"message_id": "m1", "revision": "1"}]},
            ACKS_FORMAT_ERROR,
        ),
    ],
)
def test_post_acks_validation(client, body, expected):
    response = client.post(
        "/messages/acks", json=body, headers={"x-api-key": API_KEY}
    )
    assert response.status_code == 400
    assert error(response) == expected


def test_post_acks_with_no_body(client):
    response = client.post("/messages/acks", json={}, headers={"x-api-key": API_KEY})
    assert response.status_code == 400
    assert error(response) == "Request body is required"


# ------------------------------------------------------------------ admin API


def test_admin_requires_its_own_key(client):
    response = client.get("/admin/messages")
    assert response.status_code == 401
    assert error(response) == MISSING_ADMIN_KEY_ERROR

    response = client.get("/admin/messages", headers={"x-admin-key": "wrong"})
    assert response.status_code == 401
    assert error(response) == INVALID_ADMIN_KEY_ERROR


def test_a_client_key_does_not_open_the_admin_surface(client):
    response = client.get("/admin/messages", headers={"x-api-key": API_KEY})
    assert response.status_code == 401
    assert error(response) == MISSING_ADMIN_KEY_ERROR


def test_admin_create_read_update_delete(client, admin_auth):
    created = client.post("/admin/messages", json=make_payload(), headers=admin_auth)
    assert created.status_code == 201
    message_id = created.get_json()["message_id"]
    assert created.get_json()["revision"] == 1

    fetched = client.get(f"/admin/messages/{message_id}", headers=admin_auth)
    assert fetched.status_code == 200
    assert fetched.get_json()["title"] == "Lagoon closed Tuesday"

    updated = client.put(
        f"/admin/messages/{message_id}",
        json=make_payload(title="Reworded", bump_revision=True),
        headers=admin_auth,
    )
    assert updated.status_code == 200
    assert updated.get_json()["title"] == "Reworded"
    assert updated.get_json()["revision"] == 2

    deleted = client.delete(f"/admin/messages/{message_id}", headers=admin_auth)
    assert deleted.status_code == 200
    assert deleted.get_json() == {"message": "Message deleted"}

    assert client.get(f"/admin/messages/{message_id}", headers=admin_auth).status_code == 404


def test_an_edit_does_not_re_show_by_default(client, admin_auth):
    created = client.post("/admin/messages", json=make_payload(), headers=admin_auth)
    message_id = created.get_json()["message_id"]

    updated = client.put(
        f"/admin/messages/{message_id}",
        json=make_payload(title="Typo fixed"),
        headers=admin_auth,
    )
    assert updated.get_json()["revision"] == 1


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/admin/messages/nope"),
        ("put", "/admin/messages/nope"),
        ("delete", "/admin/messages/nope"),
        ("post", "/admin/messages/nope/enabled"),
    ],
)
def test_admin_not_found(client, admin_auth, method, path):
    call = getattr(client, method)
    kwargs = {"headers": admin_auth}
    if method in ("put", "post"):
        kwargs["json"] = make_payload(enabled=False)
    response = call(path, **kwargs)
    assert response.status_code == 404
    assert error(response) == NOT_FOUND_ERROR


def test_admin_validation_errors_reach_the_caller(client, admin_auth):
    response = client.post(
        "/admin/messages", json=make_payload(display="popup"), headers=admin_auth
    )
    assert response.status_code == 400
    assert error(response) == "Invalid display. Must be 'modal', 'banner', or 'inbox'"


def test_admin_list_carries_ack_and_audience_counts(client, admin_auth, services):
    message = seed(services)
    services.messages.record_acks(CLIENT_ID, [(message.message_id, 1)])

    listed = client.get("/admin/messages", headers=admin_auth).get_json()["messages"]
    assert len(listed) == 1
    assert listed[0]["ack_count"] == 1
    # No upstream database in this fixture, so there is no client base to count.
    assert listed[0]["matched_client_count"] == 0
    assert listed[0]["client_ids"] is None


def test_admin_enabled_toggle(client, admin_auth, services):
    message = seed(services)

    disabled = client.post(
        f"/admin/messages/{message.message_id}/enabled",
        json={"enabled": False},
        headers=admin_auth,
    )
    assert disabled.status_code == 200
    assert disabled.get_json()["enabled"] is False
    assert disabled.get_json()["revision"] == 1

    response = client.post(
        f"/admin/messages/{message.message_id}/enabled",
        json={"nope": True},
        headers=admin_auth,
    )
    assert response.status_code == 400
    assert error(response) == "Missing required field: enabled"


def test_admin_audience_dry_run(tmp_path: Path, config_path: Path, admin_auth):
    upstream = build_upstream_db(
        tmp_path / "water_temperature.db",
        [
            {"uuid": CLIENT_ID, "client_os": "ios", "days_count": 40},
            {"uuid": OTHER_ID, "client_os": "android", "days_count": 2},
        ],
    )
    settings = Settings(
        db_path=tmp_path / "messages.db",
        config_path=config_path,
        upstream_db_path=upstream,
        auth_disabled=False,
        ttl_seconds=900,
    )
    app = create_app(services=Services.build(settings=settings))
    app.config.update(TESTING=True)
    http = app.test_client()

    everyone = http.post("/admin/audience", json={"os": []}, headers=admin_auth)
    assert everyone.status_code == 200
    assert everyone.get_json()["count"] == 2

    ios_only = http.post("/admin/audience", json={"os": ["ios"]}, headers=admin_auth)
    assert ios_only.get_json()["count"] == 1
    assert ios_only.get_json()["sample"] == [CLIENT_ID]


def test_admin_audience_needs_no_title_or_body(client, admin_auth):
    """An operator sets up who it reaches before writing what it says."""
    response = client.post(
        "/admin/audience", json={"min_days_count": 5}, headers=admin_auth
    )
    assert response.status_code == 200
    assert response.get_json() == {"count": 0, "sample": []}


def test_admin_audience_validates_with_the_same_strings(client, admin_auth):
    response = client.post(
        "/admin/audience", json={"os": ["windows"]}, headers=admin_auth
    )
    assert response.status_code == 400
    assert error(response) == "Invalid os. Expected a list of 'android', 'ios' or 'web'"


# -------------------------------------------------------------------- startup


def test_refuses_to_start_with_no_api_keys(settings: Settings):
    """A container that cannot authenticate must not serve, it must exit."""
    settings.config_path.write_text(yaml.safe_dump({"api_keys": []}))

    with pytest.raises(RuntimeError) as excinfo:
        create_app(services=Services.build(settings=settings))
    assert "No api_keys found" in str(excinfo.value)


def test_without_admin_keys_the_admin_routes_do_not_exist(settings: Settings):
    """Missing admin keys degrade the deploy; they must not open it."""
    settings.config_path.write_text(yaml.safe_dump({"api_keys": [API_KEY]}))
    services = Services.build(settings=settings)
    assert admin_enabled(services) is False

    app = create_app(services=services)
    app.config.update(TESTING=True)
    http = app.test_client()

    assert http.get("/health").status_code == 200
    # 404, not 401: the surface is absent rather than merely guarded.
    assert http.get("/admin/messages", headers={"x-admin-key": ADMIN_KEY}).status_code == 404


def test_admin_enabled_when_configured(services):
    assert admin_enabled(services) is True
    assert services.config.admin_keys() == [ADMIN_KEY]


# -------------------------------------------------------------------- preview


def test_admin_preview_returns_the_parse_tree(client, admin_auth):
    response = client.post(
        "/admin/preview",
        json={"body": "Closed **Tuesday**.\n\n- morning\n- [book](https://thewave.com)"},
        headers=admin_auth,
    )
    assert response.status_code == 200

    blocks = response.get_json()["blocks"]
    assert [block["kind"] for block in blocks] == ["paragraph", "bullets"]
    assert blocks[0]["items"][0] == [
        {"kind": "text", "text": "Closed ", "url": None},
        {"kind": "bold", "text": "Tuesday", "url": None},
        {"kind": "text", "text": ".", "url": None},
    ]
    assert blocks[1]["items"][1][0] == {
        "kind": "link",
        "text": "book",
        "url": "https://thewave.com",
    }


def test_admin_preview_reports_what_a_save_would_reject(client, admin_auth):
    response = client.post(
        "/admin/preview", json={"body": "**unclosed"}, headers=admin_auth
    )
    assert response.status_code == 400
    assert error(response) == "Unclosed bold in body: '**' with no matching '**'"


def test_admin_preview_applies_the_length_limit(client, admin_auth):
    response = client.post(
        "/admin/preview", json={"body": "x" * 2001}, headers=admin_auth
    )
    assert response.status_code == 400
    assert error(response) == "body must be at most 2000 characters"


def test_admin_preview_needs_a_body(client, admin_auth):
    response = client.post("/admin/preview", json={"body": 7}, headers=admin_auth)
    assert response.status_code == 400
    assert error(response) == "Invalid body. Expected text"


# --------------------------------------------------- banner dismissal delay


def test_dismissal_fields_reach_the_client(client, services):
    seed(services, display="banner", dismissable_at="2026-10-01T09:00:00Z",
         dismissable_after="30s")

    message = client.get("/messages", headers=headers()).get_json()["messages"][0]
    assert message["dismissable_at"] == "2026-10-01T09:00:00+00:00"
    assert message["dismissable_after"] == "30s"


def test_they_are_null_rather_than_absent_on_a_modal(client, services):
    """One shape for the client to read, not two keyed on display."""
    seed(services, display="modal")

    message = client.get("/messages", headers=headers()).get_json()["messages"][0]
    assert message["dismissable_at"] is None
    assert message["dismissable_after"] is None


def test_admin_rejects_a_delay_on_a_modal(client, admin_auth):
    response = client.post(
        "/admin/messages",
        json=make_payload(display="modal", dismissable_after="30s"),
        headers=admin_auth,
    )
    assert response.status_code == 400
    assert error(response) == "dismissable_after is only valid for banner messages"


def test_admin_rejects_a_dismissal_later_than_expiry(client, admin_auth):
    response = client.post(
        "/admin/messages",
        json=make_payload(
            display="banner",
            starts_at="2026-10-01T09:00:00Z",
            ends_at="2026-10-05T09:00:00Z",
            expires_at="2026-10-06T09:00:00Z",
            dismissable_at="2026-10-07T09:00:00Z",
        ),
        headers=admin_auth,
    )
    assert response.status_code == 400
    assert error(response) == "dismissable_at must not be later than expires_at"


def test_admin_round_trips_the_delay(client, admin_auth):
    created = client.post(
        "/admin/messages",
        json=make_payload(display="banner", dismissable_after="  5M  "),
        headers=admin_auth,
    )
    assert created.status_code == 201
    assert created.get_json()["dismissable_after"] == "5m"
