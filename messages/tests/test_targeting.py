"""Each axis in isolation, both edge cases, ack suppression, and sort order.

No database anywhere in this file — that is the point of ``targeting`` being
pure, and it is what lets the edge cases be tested as rules rather than as
side effects of a query.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from src.targeting import DEFAULT_DAYS_COUNT, Client, matches, select

from conftest import NOW, make_message

CLIENT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ID = "22222222-2222-2222-2222-222222222222"


def caller(**overrides) -> Client:
    fields = dict(client_id=CLIENT_ID, client_os="ios", client_version="1.2.3")
    fields.update(overrides)
    return Client.build(**fields)


def served(message, client=None, acked=None) -> bool:
    return matches(message, client or caller(), acked, NOW)


def test_an_untargeted_message_goes_to_everyone():
    assert served(make_message()) is True


def test_disabled_is_never_served():
    assert served(make_message(enabled=False)) is False


# ------------------------------------------------------------------- window


def test_window_start_is_inclusive_and_end_exclusive():
    assert served(make_message(starts_at=NOW.isoformat())) is True
    assert served(make_message(starts_at=(NOW + timedelta(seconds=1)).isoformat())) is False
    assert served(make_message(ends_at=(NOW + timedelta(hours=1)).isoformat())) is True
    assert served(make_message(ends_at=NOW.isoformat())) is False


def test_no_end_means_no_end():
    assert served(make_message(ends_at=None)) is True


def test_an_unparseable_start_matches_nothing():
    """Only reachable by hand-editing a row; better invisible than untimed."""
    assert served(make_message(starts_at="whenever")) is False


# ------------------------------------------------------------- client_ids/os


def test_client_ids():
    assert served(make_message(client_ids=[CLIENT_ID])) is True
    assert served(make_message(client_ids=[OTHER_ID])) is False
    assert served(make_message(client_ids=[OTHER_ID, CLIENT_ID])) is True


def test_os():
    assert served(make_message(os=["ios"])) is True
    assert served(make_message(os=["android"])) is False
    assert served(make_message(os=["android", "ios"])) is True


def test_a_missing_os_header_fails_closed_on_the_constraint():
    anonymous = caller(client_os=None)
    assert served(make_message(), anonymous) is True
    assert served(make_message(os=["ios"]), anonymous) is False


def test_os_is_compared_lowercased():
    assert served(make_message(os=["ios"]), caller(client_os="iOS")) is True


# ----------------------------------------------------------------- version


def test_version_bounds_are_inclusive():
    assert served(make_message(min_version="1.2.3")) is True
    assert served(make_message(max_version="1.2.3")) is True
    assert served(make_message(min_version="1.2.4")) is False
    assert served(make_message(max_version="1.2.2")) is False


def test_version_comparison_is_numeric():
    """The reason this is not a SQL BETWEEN: "1.0.9" sorts above "1.0.10"."""
    assert served(make_message(min_version="1.0.10"), caller(client_version="1.0.9")) is False
    assert served(make_message(min_version="1.0.9"), caller(client_version="1.0.10")) is True


@pytest.mark.parametrize("version", [None, "", "1.2.3-beta"])
def test_edge_case_1_unparseable_version(version):
    """Excluded from any message that sets a bound; included when neither is set."""
    unknown = caller(client_version=version)
    assert served(make_message(), unknown) is True
    assert served(make_message(min_version="1.0.0"), unknown) is False
    assert served(make_message(max_version="9.0.0"), unknown) is False


# -------------------------------------------------------------- days_count


def test_days_count_bounds_are_inclusive():
    established = caller(days_count=30)
    assert served(make_message(min_days_count=30), established) is True
    assert served(make_message(max_days_count=30), established) is True
    assert served(make_message(min_days_count=31), established) is False
    assert served(make_message(max_days_count=29), established) is False


def test_edge_case_2_an_unknown_client_is_maximally_new():
    """A fresh install may call /messages before it has ever called /calendar."""
    unknown = Client.build(client_id=CLIENT_ID, days_count=None)
    assert unknown.days_count == DEFAULT_DAYS_COUNT

    welcome = make_message(max_days_count=1)
    assert matches(welcome, unknown, None, NOW) is True


# --------------------------------------------------------------------- acks


def test_an_ack_at_the_current_revision_suppresses():
    message = make_message(revision=1)
    assert served(message, acked=None) is True
    assert served(message, acked=1) is False


def test_a_revision_bump_re_serves_an_acked_message():
    assert served(make_message(revision=2), acked=1) is True


def test_an_ack_ahead_of_the_revision_still_suppresses():
    """A client that acked revision 3 has seen more than revision 2's wording."""
    assert served(make_message(revision=2), acked=3) is False


# --------------------------------------------------------------------- sort


def test_select_orders_by_priority_then_recency_then_id():
    low = make_message(message_id="a", priority=0, starts_at="2026-05-01T00:00:00+00:00")
    high = make_message(message_id="b", priority=5, starts_at="2026-01-01T00:00:00+00:00")
    newer = make_message(message_id="c", priority=0, starts_at="2026-05-20T00:00:00+00:00")

    order = select([low, high, newer], caller(), {}, NOW)
    assert [message.message_id for message in order] == ["b", "c", "a"]


def test_select_tie_breaks_on_message_id_so_the_order_is_stable():
    first = make_message(message_id="aaa")
    second = make_message(message_id="bbb")
    assert [m.message_id for m in select([second, first], caller(), {}, NOW)] == [
        "aaa",
        "bbb",
    ]


def test_select_applies_the_ack_map_per_message():
    seen = make_message(message_id="seen", revision=1)
    unseen = make_message(message_id="unseen", revision=1)

    order = select([seen, unseen], caller(), {"seen": 1}, NOW)
    assert [message.message_id for message in order] == ["unseen"]


def test_axes_and_together():
    """Every rule narrows; none of them widens another."""
    message = make_message(os=["ios"], min_version="1.0.0", max_days_count=1)
    assert served(message, caller(days_count=1)) is True
    assert served(message, caller(days_count=1, client_os="android")) is False
    assert served(message, caller(days_count=1, client_version="0.9.0")) is False
    assert served(message, caller(days_count=2)) is False


# ------------------------------------------------- retained messages refresh


def test_a_retained_message_keeps_being_served_after_it_is_acked():
    """The only channel by which a filed message can be told to leave an inbox.

    `expires_at` is an instruction the client acts on out of its stored copy, so
    an edit to it reaches nobody unless the message is still arriving.
    """
    assert served(make_message(retain=True), acked=1) is True


def test_a_non_retained_message_still_stops_at_its_ack():
    """Shown once and dropped, so there is nothing to refresh."""
    assert served(make_message(retain=False), acked=1) is False


def test_the_refresh_stops_when_the_message_stops_being_live():
    """`ends_at` is the propagation window; past it there is no channel left."""
    ended = make_message(retain=True, ends_at=(NOW - timedelta(hours=1)).isoformat())
    assert served(ended, acked=1) is False

    disabled = make_message(retain=True, enabled=False)
    assert served(disabled, acked=1) is False


def test_the_refresh_still_respects_targeting():
    """A refresh is a delivery, so it narrows the same way one does."""
    message = make_message(retain=True, os=["android"])
    assert served(message, acked=1) is False


def test_an_expiry_inside_the_window_is_what_expires_it_from_inboxes():
    """The whole point: the client receives the new expires_at and prunes.

    The server does not stop serving it — it cannot, or the client would never
    hear. Pruning is the client's, out of the value it was just handed.
    """
    expiring = make_message(
        retain=True,
        ends_at=(NOW + timedelta(days=30)).isoformat(),
        expires_at=(NOW - timedelta(minutes=1)).isoformat(),
    )
    assert served(expiring, acked=1) is True


def test_a_revision_bump_is_still_what_makes_it_show_again():
    """Refreshing is not re-showing; the client's seen-set keys on the revision."""
    assert served(make_message(retain=True, revision=2), acked=1) is True
    assert served(make_message(retain=False, revision=2), acked=1) is True
