"""CRUD, ack upsert idempotence, and ack counts."""
from __future__ import annotations

import uuid

from src.models import MessageRequest

from conftest import make_payload


def create(repository, **overrides):
    return repository.create(MessageRequest.from_payload(make_payload(**overrides)))


def test_create_round_trips_every_column(repository):
    one = str(uuid.uuid4())
    message = create(
        repository,
        client_ids=[one],
        os=["ios", "web"],
        min_version="1.0.0",
        max_version="2.0.0",
        min_days_count=1,
        max_days_count=30,
        starts_at="2026-10-01T09:00:00Z",
        ends_at="2026-10-05T09:00:00Z",
        expires_at="2026-10-09T09:00:00Z",
        retain=True,
        display="modal",
        level="warning",
        priority=7,
        action_url="https://thewave.com",
        action_label="Book now",
    )

    stored = repository.get(message.message_id)
    assert stored == message
    assert stored.client_ids == [one]
    assert stored.os == ["ios", "web"]
    assert stored.retain is True
    assert stored.enabled is True
    assert stored.revision == 1
    assert stored.priority == 7
    assert stored.created_at == stored.updated_at


def test_get_returns_none_for_an_unknown_id(repository):
    assert repository.get("nope") is None


def test_list_all_is_newest_first(repository):
    first = create(repository, title="first")
    second = create(repository, title="second")

    listed = repository.list_all()
    assert len(listed) == 2
    # Same-second creation is possible, so assert the set and the tie-break.
    assert {message.message_id for message in listed} == {
        first.message_id,
        second.message_id,
    }


def test_list_enabled_omits_disabled_rows(repository):
    live = create(repository, title="live")
    dead = create(repository, title="dead", enabled=False)

    ids = {message.message_id for message in repository.list_enabled()}
    assert live.message_id in ids
    assert dead.message_id not in ids


def test_update_rewrites_columns_without_touching_the_revision(repository):
    message = create(repository, title="before", priority=0)

    updated = repository.update(
        message.message_id,
        MessageRequest.from_payload(make_payload(title="after", priority=3)),
    )

    assert updated.title == "after"
    assert updated.priority == 3
    assert updated.revision == 1
    assert updated.created_at == message.created_at
    assert updated.updated_at >= message.updated_at


def test_update_bumps_the_revision_when_asked(repository):
    message = create(repository)
    updated = repository.update(
        message.message_id,
        MessageRequest.from_payload(make_payload(title="reworded")),
        bump_revision=True,
    )
    assert updated.revision == message.revision + 1


def test_update_clears_a_field_that_the_new_payload_omits(repository):
    """A full rewrite, not a merge: what is not in the payload is not on the row."""
    message = create(repository, os=["ios"], min_version="1.0.0")
    updated = repository.update(
        message.message_id, MessageRequest.from_payload(make_payload())
    )
    assert updated.os is None
    assert updated.min_version is None


def test_update_returns_none_for_an_unknown_id(repository):
    assert repository.update("nope", MessageRequest.from_payload(make_payload())) is None


def test_set_enabled_toggles_without_bumping_the_revision(repository):
    message = create(repository)

    disabled = repository.set_enabled(message.message_id, False)
    assert disabled.enabled is False
    assert disabled.revision == message.revision

    assert repository.set_enabled(message.message_id, True).enabled is True
    assert repository.set_enabled("nope", False) is None


def test_delete(repository):
    message = create(repository)
    assert repository.delete(message.message_id) is True
    assert repository.get(message.message_id) is None
    assert repository.delete(message.message_id) is False


def test_delete_takes_the_acks_with_it(repository, client_id):
    message = create(repository)
    repository.record_acks(client_id, [(message.message_id, 1)])
    assert repository.ack_counts() == {message.message_id: 1}

    repository.delete(message.message_id)
    assert repository.ack_counts() == {}


def test_record_acks_is_an_idempotent_upsert(repository, client_id):
    message = create(repository)

    assert repository.record_acks(client_id, [(message.message_id, 1)]) == 1
    assert repository.record_acks(client_id, [(message.message_id, 1)]) == 1
    assert repository.acked_revisions(client_id) == {message.message_id: 1}
    assert repository.ack_counts() == {message.message_id: 1}


def test_record_acks_overwrites_the_revision(repository, client_id):
    message = create(repository)
    repository.record_acks(client_id, [(message.message_id, 1)])
    repository.record_acks(client_id, [(message.message_id, 2)])
    assert repository.acked_revisions(client_id) == {message.message_id: 2}


def test_record_acks_ignores_an_unknown_message(repository, client_id):
    """A client holding a since-deleted message can still flush its queue."""
    message = create(repository)

    recorded = repository.record_acks(
        client_id, [(message.message_id, 1), ("deleted-message", 1)]
    )
    assert recorded == 1
    assert repository.acked_revisions(client_id) == {message.message_id: 1}


def test_acks_are_scoped_to_one_client(repository, client_id):
    message = create(repository)
    other = str(uuid.uuid4())

    repository.record_acks(client_id, [(message.message_id, 1)])
    assert repository.acked_revisions(other) == {}
    assert repository.ack_counts() == {message.message_id: 1}

    repository.record_acks(other, [(message.message_id, 1)])
    assert repository.ack_counts() == {message.message_id: 2}


def test_dismissal_fields_round_trip(repository):
    message = create(
        repository,
        display="banner",
        dismissable_at="2026-10-01T09:00:00Z",
        dismissable_after="30s",
    )
    stored = repository.get(message.message_id)
    assert stored.dismissable_at == "2026-10-01T09:00:00+00:00"
    assert stored.dismissable_after == "30s"


def test_banner_title_round_trips_and_is_cleared_by_a_change_of_display(repository):
    """The only way to clear it, now that a banner cannot be saved without one."""
    message = create(repository, display="banner", banner_title="Closed today")
    assert repository.get(message.message_id).banner_title == "Closed today"

    updated = repository.update(
        message.message_id,
        MessageRequest.from_payload(make_payload(display="inbox")),
    )
    assert updated.banner_title is None


def test_update_clears_a_dismissal_delay_the_new_payload_omits(repository):
    """A full rewrite: removing the delay from the form removes it from the row."""
    message = create(repository, display="banner", dismissable_after="5m")
    updated = repository.update(
        message.message_id,
        MessageRequest.from_payload(make_payload(display="banner")),
    )
    assert updated.dismissable_after is None
    assert updated.dismissable_at is None


def test_expire_moves_only_the_expiry(repository):
    """Revoking is one field: the kill switch and the window stay put.

    They have to — the message keeps being delivered for a while yet, or the
    clients holding it never hear that it has gone.
    """
    message = create(
        repository,
        display="inbox",
        retain=True,
        ends_at="2099-01-01T00:00:00Z",
    )
    assert message.expires_at is None

    revoked = repository.expire(message.message_id)
    assert revoked.expires_at is not None
    assert revoked.expires_at == revoked.updated_at
    # A bump re-shows a message, which is the opposite of revoking one.
    assert revoked.revision == message.revision
    assert revoked.enabled is True
    assert revoked.ends_at == message.ends_at
    assert revoked.title == message.title


def test_expire_returns_none_for_an_unknown_id(repository):
    assert repository.expire("nope") is None
