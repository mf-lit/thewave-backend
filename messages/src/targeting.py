"""Who sees a message.

A pure module: no database, no Flask, no clock of its own. The repository
loads the enabled rows and the calling client's ack map, and everything else
is decided here — which is what makes the whole rule set unit-testable without
a database, and is the reason the two edge cases below can be tested directly.

**Filtering happens in Python, not SQL**, deliberately. The table holds tens of
rows, so there is nothing to gain from pushing it down, and two of the axes are
wrong in SQL: version comparison under string ordering makes ``"1.0.9"``
greater than ``"1.0.10"``, and ``client_ids``/``os`` are JSON.

Every axis narrows independently and they AND together. A message that sets
none of them goes to everyone, which is the common case — a closure notice or
a release note.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List, Mapping, Optional, Protocol

from . import versions
from .clock import parse_iso
from .models import Message


class AudienceRules(Protocol):
    """The six fields that describe *who*, with no reference to when.

    Both a stored ``Message`` and an unsaved ``AudienceRequest`` satisfy this,
    which is what lets the dashboard count a draft's audience with the same
    code that serves the real thing — rather than a second implementation that
    can quietly disagree with the first.
    """

    client_ids: Optional[List[str]]
    os: Optional[List[str]]
    min_version: Optional[str]
    max_version: Optional[str]
    min_days_count: Optional[int]
    max_days_count: Optional[int]

# What a client with no row in upstream-api's `clients` table is treated as.
# A fresh install may call /messages before it has ever called /calendar, and
# "maximally new" is the honest reading of a client we have never seen.
DEFAULT_DAYS_COUNT = 1


@dataclass(frozen=True)
class Client:
    """The caller, as the targeting rules see them."""

    client_id: str
    client_os: Optional[str] = None
    client_version: Optional[str] = None
    days_count: int = DEFAULT_DAYS_COUNT

    @classmethod
    def build(
        cls,
        client_id: str,
        client_os: Optional[str] = None,
        client_version: Optional[str] = None,
        days_count: Optional[int] = None,
    ) -> "Client":
        """Assemble a caller from request headers and a directory lookup.

        ``days_count`` is Optional here and not on the dataclass because None
        is what the directory returns for an unknown client — the substitution
        belongs at the boundary, once, rather than at every read.
        """
        return cls(
            client_id=client_id,
            client_os=client_os.strip().lower() if isinstance(client_os, str) else None,
            client_version=client_version,
            days_count=DEFAULT_DAYS_COUNT if days_count is None else days_count,
        )


def _within_window(message: Message, now: datetime) -> bool:
    """Whether ``now`` falls in ``[starts_at, ends_at)``.

    The start is inclusive and the end exclusive, so a message set to end at
    the moment another starts does not show both.

    An unparseable ``starts_at`` matches nothing. The column is NOT NULL and
    validated on write, so the only way to get one is a hand-edited row in
    sqlite-web — and a message nobody can see is a far better failure than one
    that goes to everyone at a time nobody chose.
    """
    starts_at = parse_iso(message.starts_at)
    if starts_at is None or starts_at > now:
        return False

    if message.ends_at is None:
        return True
    ends_at = parse_iso(message.ends_at)
    return ends_at is not None and now < ends_at


def is_being_delivered(message: Message, now: datetime) -> bool:
    """Whether the service is still sending this message to anyone at all.

    The half of ``matches`` that does not depend on who is asking. The admin
    API calls it to answer "would a revocation reach anyone", so that question
    and the serving path cannot form different opinions about what live means.
    """
    return message.enabled and _within_window(message, now)


def has_expired(message: Message, now: datetime) -> bool:
    """Whether a retained message is due to be dropped from the inboxes holding it.

    Meaningless without ``retain`` — ``expires_at`` says when a filed copy
    leaves the inbox, and a message the client never files has no filed copy —
    so it is False there rather than being a second kind of end date.

    An unreadable ``expires_at`` reads as "not expired", the same direction
    ``decode_list`` fails in: a hand-edited row keeps being served rather than
    silently vanishing from every inbox.
    """
    if not message.retain or message.expires_at is None:
        return False
    expires_at = parse_iso(message.expires_at)
    return expires_at is not None and now >= expires_at


def _in_list(allowed: Optional[List[str]], value: Optional[str]) -> bool:
    """Membership in a targeting list, where an absent list constrains nothing.

    A missing ``value`` — no ``X-Client-OS`` header — is excluded whenever the
    list is set and included when it is not: the same fail-closed-on-the-
    constraint rule ``versions.in_range`` applies to a missing version.
    """
    if not allowed:
        return True
    return value is not None and value in allowed


def _in_count_range(
    value: int, minimum: Optional[int], maximum: Optional[int]
) -> bool:
    """Inclusive on both ends, and unbounded on an end left as None."""
    if minimum is not None and value < minimum:
        return False
    return maximum is None or value <= maximum


def audience_matches(rules: AudienceRules, client: Client) -> bool:
    """Whether ``client`` is in the audience these rules describe.

    Who, not when: nothing here reads ``enabled``, the window, or the acks. It
    is the whole of what the admin audience count asks, and part of what
    ``matches`` asks.
    """
    if not _in_list(rules.client_ids, client.client_id):
        return False
    if not _in_list(rules.os, client.client_os):
        return False
    if not versions.in_range(
        client.client_version, rules.min_version, rules.max_version
    ):
        return False
    return _in_count_range(
        client.days_count, rules.min_days_count, rules.max_days_count
    )


def matches(
    message: Message,
    client: Client,
    acked_revision: Optional[int],
    now: datetime,
) -> bool:
    """Whether this one message should be served to this one client."""
    if not is_being_delivered(message, now):
        return False
    if not audience_matches(message, client):
        return False

    # An ack suppresses the revision it names and every earlier one. Bumping
    # `revision` past it is what re-serves an edited message to someone who has
    # already seen the old wording.
    #
    # An expired message is the exception, and it is what makes revoking one
    # honest. A client that has never held it must not *start* showing
    # something whose inbox copy is already due to be dropped — it would show
    # the message, file it, and prune it in the same breath. The refresh below
    # only ever needed to reach the clients already holding it, so this costs
    # that channel nothing.
    if acked_revision is None or acked_revision < message.revision:
        return not has_expired(message, now)

    # Acked at the current revision, so it has been seen. A **retained** message
    # keeps being served anyway, for as long as it is live.
    #
    # It is the only channel by which a message already filed in someone's inbox
    # can be told to leave it. `expires_at` is an instruction the client acts on
    # locally, out of the copy it stored — so an edit to it reaches nobody
    # unless the message is still arriving. Without this, a retained message's
    # expiry is fixed the moment a client files it, and "drop this from
    # everyone's inbox" is unimplementable.
    #
    # The client must not re-show it: its seen-set is keyed on
    # (message_id, revision), which already covers this, and refreshing the
    # stored copy on every poll is the whole point. A revision bump is still
    # what makes it show again.
    #
    # Non-retained messages are shown once and dropped, so there is nothing to
    # refresh and they stop here as they always have.
    #
    # Note this stays true *after* the message expires, and has to: the past
    # `expires_at` is the instruction, and a client only reads it out of a
    # payload that still carries the message. It stops when the message stops
    # being live, which is the operator's cue to disable it.
    return message.retain


def sort_key(message: Message):
    """Highest priority first, then newest, then stable.

    The client shows at most one modal per foreground and sends the rest to the
    inbox, so this order decides which one that is. ``message_id`` breaks the
    remaining ties so two messages created in the same second do not swap
    places between requests.
    """
    starts_at = parse_iso(message.starts_at)
    return (
        -message.priority,
        # Negating a datetime is not available, so sort on the ascending key
        # and reverse it by ordering the tuple's earlier fields descending.
        starts_at.timestamp() * -1 if starts_at else 0,
        message.message_id,
    )


def select(
    messages: Iterable[Message],
    client: Client,
    acked: Mapping[str, int],
    now: datetime,
) -> List[Message]:
    """The messages ``client`` should be served, in the order to show them."""
    wanted = [
        message
        for message in messages
        if matches(message, client, acked.get(message.message_id), now)
    ]
    return sorted(wanted, key=sort_key)
