"""Every validation rule and its exact message.

The strings are the dashboard's contract — the compose form shows them to the
operator verbatim — so they are asserted, not just the fact of a rejection.
"""
from __future__ import annotations

import uuid

import pytest

from src import models
from src.markdown import BOLD, unclosed_error
from src.models import MessageRequest
from src.validation import ValidationError

from conftest import make_payload


def build(**overrides) -> MessageRequest:
    return MessageRequest.from_payload(make_payload(**overrides))


def rejects(expected: str, **overrides):
    with pytest.raises(ValidationError) as excinfo:
        build(**overrides)
    assert str(excinfo.value) == expected


def test_minimal_payload_and_its_defaults():
    request = build()
    assert request.title == "Lagoon closed Tuesday"
    assert request.display == "banner"
    assert request.level == models.LEVEL_INFO
    assert request.priority == 0
    assert request.retain is False
    assert request.enabled is True
    # Everything not set targets everyone.
    assert request.client_ids is None
    assert request.os is None
    assert request.min_version is None
    assert request.max_days_count is None
    assert request.ends_at is None


def test_starts_at_defaults_to_now():
    assert build().starts_at.endswith("+00:00")


@pytest.mark.parametrize("field", models.REQUIRED_FIELDS)
def test_required_fields(field):
    payload = make_payload()
    del payload[field]
    with pytest.raises(ValidationError) as excinfo:
        MessageRequest.from_payload(payload)
    assert str(excinfo.value) == f"Missing required field: {field}"


@pytest.mark.parametrize("value", ["", "   ", None, 7, []])
def test_title_must_be_a_non_empty_string(value):
    rejects("title is required and must be a string", title=value)


def test_title_and_body_are_stripped():
    request = build(title="  spaced  ", body="  words  ")
    assert request.title == "spaced"
    assert request.body == "words"


def test_length_limits():
    rejects(
        models._length_error("title", models.MAX_TITLE_LENGTH),
        title="x" * (models.MAX_TITLE_LENGTH + 1),
    )
    rejects(
        models._length_error("body", models.MAX_BODY_LENGTH),
        body="x" * (models.MAX_BODY_LENGTH + 1),
    )
    rejects(
        models._length_error("action_label", models.MAX_ACTION_LABEL_LENGTH),
        action_url="https://thewave.com",
        action_label="x" * (models.MAX_ACTION_LABEL_LENGTH + 1),
    )


def test_body_must_parse_as_the_markdown_subset():
    rejects(unclosed_error(BOLD), body="**unclosed")


def test_display_and_level_enums():
    rejects(models.DISPLAY_ERROR, display="popup")
    rejects(models.LEVEL_ERROR, level="urgent")
    for display in models.VALID_DISPLAYS:
        assert build(display=display).display == display
    for level in models.VALID_LEVELS:
        assert build(level=level).level == level


@pytest.mark.parametrize("field", ["retain", "enabled"])
def test_flags_must_be_booleans(field):
    rejects(models._flag_error(field), **{field: 1})
    assert getattr(build(**{field: True}), field) is True
    assert getattr(build(**{field: False}), field) is False


def test_priority_must_be_an_integer():
    rejects(models.PRIORITY_ERROR, priority="high")
    rejects(models.PRIORITY_ERROR, priority=True)
    assert build(priority=5).priority == 5


def test_client_ids():
    one = str(uuid.uuid4())
    assert build(client_ids=[one]).client_ids == [one]
    rejects(models.CLIENT_IDS_ERROR, client_ids="not-a-list")
    rejects(models.CLIENT_IDS_ERROR, client_ids=[7])
    rejects("Invalid client_id in client_ids: nope", client_ids=["nope"])


def test_os_is_canonicalised_and_deduplicated():
    assert build(os=["IOS", "android", "ios"]).os == ["android", "ios"]
    rejects(models.OS_ERROR, os=["windows"])
    rejects(models.OS_ERROR, os="ios")


def test_an_empty_targeting_list_means_everyone():
    """`[]` and an absent field are the same rule, so they store the same value."""
    assert build(client_ids=[]).client_ids is None
    assert build(os=[]).os is None


def test_version_bounds():
    assert build(min_version="1.2.3").min_version == "1.2.3"
    rejects(models._version_error("min_version"), min_version="1.2.3-beta")
    rejects(models._version_error("max_version"), max_version="v2")
    rejects(models.VERSION_ORDER_ERROR, min_version="2.0.0", max_version="1.0.0")
    # Equal bounds are a legitimate single-version target.
    assert build(min_version="1.0.0", max_version="1.0.0").max_version == "1.0.0"


def test_days_count_bounds():
    assert build(min_days_count=0).min_days_count == 0
    rejects(models._count_error("min_days_count"), min_days_count=-1)
    rejects(models._count_error("max_days_count"), max_days_count="lots")
    rejects(models._count_error("min_days_count"), min_days_count=True)
    rejects(models.DAYS_COUNT_ORDER_ERROR, min_days_count=5, max_days_count=2)


def test_timestamps_are_canonicalised_to_aware_utc():
    assert build(starts_at="2026-10-01T09:00:00Z").starts_at == "2026-10-01T09:00:00+00:00"
    assert build(starts_at="2026-10-01T09:00:00").starts_at == "2026-10-01T09:00:00+00:00"
    rejects(models._timestamp_error("starts_at"), starts_at="next Tuesday")
    rejects(models._timestamp_error("ends_at"), ends_at="soon")


def test_window_ordering():
    rejects(
        models.STARTS_BEFORE_ENDS_ERROR,
        starts_at="2026-10-02T09:00:00Z",
        ends_at="2026-10-01T09:00:00Z",
    )
    rejects(
        models.STARTS_BEFORE_ENDS_ERROR,
        starts_at="2026-10-01T09:00:00Z",
        ends_at="2026-10-01T09:00:00Z",
    )
    # `expires_at` is unconstrained by `ends_at` in either direction. Pulling it
    # back inside the delivery window is how a retained message is expired out
    # of inboxes that already hold it — see test_targeting.
    assert build(
        starts_at="2026-10-01T09:00:00Z",
        ends_at="2026-10-05T09:00:00Z",
        expires_at="2026-10-03T09:00:00Z",
    ).expires_at == "2026-10-03T09:00:00+00:00"
    assert build(expires_at="2026-10-03T09:00:00Z").expires_at is not None


def test_action_pair_is_both_or_neither():
    rejects(models.ACTION_PAIR_ERROR, action_url="https://thewave.com")
    rejects(models.ACTION_PAIR_ERROR, action_label="Book now")
    rejects(
        models.ACTION_URL_SCHEME_ERROR,
        action_url="http://thewave.com",
        action_label="Book now",
    )
    request = build(action_url="https://thewave.com", action_label="Book now")
    assert (request.action_url, request.action_label) == (
        "https://thewave.com",
        "Book now",
    )


def test_decode_list_never_raises_on_a_hand_edited_row():
    assert models.decode_list(None) is None
    assert models.decode_list("") is None
    assert models.decode_list("[]") is None
    assert models.decode_list("not json") is None
    assert models.decode_list('{"a": 1}') is None
    assert models.decode_list('["ios", 7]') == ["ios"]
    assert models.decode_list('["ios"]') == ["ios"]


def test_encode_list_round_trips():
    assert models.decode_list(models.encode_list(["ios", "web"])) == ["ios", "web"]
    assert models.encode_list(None) is None


# ------------------------------------------------------- banner dismissal delay


def banner(**overrides):
    """A banner, since the dismissal fields are valid on nothing else."""
    return build(display="banner", **overrides)


def test_dismissal_fields_default_to_none():
    request = banner()
    assert request.dismissable_at is None
    assert request.dismissable_after is None


@pytest.mark.parametrize(
    "value, expected",
    [
        ("30s", "30s"),
        ("5m", "5m"),
        ("2h", "2h"),
        ("  5M  ", "5m"),
        ("1s", "1s"),
        ("0s", "0s"),
        ("999h", "999h"),
    ],
)
def test_dismissable_after_is_canonicalised(value, expected):
    assert banner(dismissable_after=value).dismissable_after == expected


@pytest.mark.parametrize("value", ["30", "s", "30 s", "1.5m", "-5m", "30d", 30, True, ""])
def test_dismissable_after_format(value):
    rejects(models.DISMISSABLE_AFTER_FORMAT_ERROR, display="banner", dismissable_after=value)


@pytest.mark.parametrize("value", ["0s", "1s", "1440m", "25h", "999h"])
def test_dismissable_after_is_unbounded(value):
    """Only the shape is checked.

    How long a banner stays unavoidable is the operator's call, and the window
    bounds it in practice anyway — once `ends_at` passes it stops being served.
    `0s` is accepted and means no delay, the same as leaving it blank.
    """
    assert banner(dismissable_after=value).dismissable_after == value


def test_dismissable_at_is_canonicalised_to_aware_utc():
    request = banner(dismissable_at="2026-10-01T09:00:00Z")
    assert request.dismissable_at == "2026-10-01T09:00:00+00:00"
    rejects(
        models._timestamp_error("dismissable_at"),
        display="banner",
        dismissable_at="in a bit",
    )


def test_dismissable_at_may_not_be_later_than_expires_at():
    rejects(
        models.DISMISSABLE_AT_AFTER_EXPIRES_ERROR,
        display="banner",
        starts_at="2026-10-01T09:00:00Z",
        ends_at="2026-10-05T09:00:00Z",
        expires_at="2026-10-06T09:00:00Z",
        dismissable_at="2026-10-07T09:00:00Z",
    )
    # Equal is allowed: it becomes dismissable exactly as it expires.
    assert banner(
        starts_at="2026-10-01T09:00:00Z",
        ends_at="2026-10-05T09:00:00Z",
        expires_at="2026-10-06T09:00:00Z",
        dismissable_at="2026-10-06T09:00:00Z",
    ).dismissable_at == "2026-10-06T09:00:00+00:00"


def test_a_null_expires_at_constrains_nothing():
    """Never expiring is not something a dismissal time can be later than."""
    assert banner(dismissable_at="2099-01-01T00:00:00Z").dismissable_at is not None


@pytest.mark.parametrize("display", ["modal", "inbox"])
@pytest.mark.parametrize("field, value", [("dismissable_at", "2026-10-01T09:00:00Z"),
                                          ("dismissable_after", "30s")])
def test_dismissal_fields_are_rejected_on_other_display_types(display, field, value):
    """Rejected, not ignored — a silently dropped delay is one nobody sees fail."""
    rejects(models._dismissal_only_error(field), display=display, **{field: value})


@pytest.mark.parametrize("display", ["modal", "inbox"])
def test_an_explicit_null_is_accepted_on_other_display_types(display):
    """A serialiser that emits nulls for absent fields is not a client bug."""
    request = build(display=display, dismissable_at=None, dismissable_after=None)
    assert request.dismissable_at is None


def test_both_may_be_set_together():
    request = banner(dismissable_at="2026-10-01T09:00:00Z", dismissable_after="30s")
    assert request.dismissable_at == "2026-10-01T09:00:00+00:00"
    assert request.dismissable_after == "30s"


@pytest.mark.parametrize(
    "value, expected",
    [("30s", 30), ("5m", 300), ("2h", 7200), ("nonsense", None), (None, None), (7, None)],
)
def test_duration_seconds_never_raises_on_a_hand_edited_row(value, expected):
    assert models.duration_seconds(value) == expected
