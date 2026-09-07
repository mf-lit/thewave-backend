"""Domain models and request validation.

The error strings here are the admin API's contract — the dashboard's compose
form surfaces them directly — so ``tests/test_api_contract.py`` pins every one
of them verbatim, and the order the checks run in is part of that: the first
failure wins, and a missing field beats a malformed one.

Two shapes come out of a stored ``Message``. ``to_api()`` is what a client
sees: the message and how to show it, and nothing about who else got it.
``to_admin_api()`` is everything, including the targeting rules, and is
reachable only over the Docker network.
"""
from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from . import markdown, versions
from .clock import parse_iso, utc_now_iso
from .validation import ValidationError

DISPLAY_MODAL = "modal"
DISPLAY_BANNER = "banner"
DISPLAY_INBOX = "inbox"
VALID_DISPLAYS: Tuple[str, ...] = (DISPLAY_MODAL, DISPLAY_BANNER, DISPLAY_INBOX)

LEVEL_INFO = "info"
LEVEL_WARNING = "warning"
VALID_LEVELS: Tuple[str, ...] = (LEVEL_INFO, LEVEL_WARNING)

OS_ANDROID = "android"
OS_IOS = "ios"
OS_WEB = "web"
VALID_OS: Tuple[str, ...] = (OS_ANDROID, OS_IOS, OS_WEB)

REQUIRED_FIELDS: Tuple[str, ...] = ("title", "body", "display")

MAX_TITLE_LENGTH = 100
MAX_BODY_LENGTH = 2000
MAX_ACTION_LABEL_LENGTH = 30

URL_SCHEME = markdown.URL_SCHEME

DISPLAY_ERROR = "Invalid display. Must be 'modal', 'banner', or 'inbox'"
LEVEL_ERROR = "Invalid level. Must be 'info' or 'warning'"
OS_ERROR = "Invalid os. Expected a list of 'android', 'ios' or 'web'"
CLIENT_IDS_ERROR = "Invalid client_ids. Expected a list of client UUIDs"
PRIORITY_ERROR = "priority must be an integer"
ACTION_PAIR_ERROR = "action_url and action_label must be set together, or neither"
ACTION_URL_SCHEME_ERROR = f"Invalid action_url. URLs must start with '{URL_SCHEME}'"
STARTS_BEFORE_ENDS_ERROR = "starts_at must be earlier than ends_at"
VERSION_ORDER_ERROR = "min_version must not be greater than max_version"
DAYS_COUNT_ORDER_ERROR = "min_days_count must not be greater than max_days_count"

# The two fields that hold a banner on screen. Banner-only, and rejected rather
# than ignored on the other two display types: a modal is dismissed by tapping
# it and an inbox entry is never dismissed at all, so either would be a silent
# no-op — the operator would set a delay, see it stored, and never see it work.
DISMISSAL_FIELDS: Tuple[str, ...] = ("dismissable_at", "dismissable_after")

_DURATION_UNITS = {"s": 1, "m": 60, "h": 3600}
_DURATION = re.compile(r"^(\d+)([smh])$")

DISMISSABLE_AFTER_FORMAT_ERROR = (
    "Invalid dismissable_after. Expected a whole number of seconds, minutes or "
    "hours, e.g. '30s', '5m', '2h'"
)
DISMISSABLE_AT_AFTER_EXPIRES_ERROR = (
    "dismissable_at must not be later than expires_at"
)


def _dismissal_only_error(field: str) -> str:
    return f"{field} is only valid for banner messages"


def normalize_duration(value: Any) -> str:
    """Accept ``30s`` / ``5M`` / ``2h``; return it canonical and lowercased.

    Only the shape is checked. There is deliberately no upper bound: how long a
    banner should stay unavoidable is the operator's call, and a message that
    cannot be dismissed for the length of a closure is a thing they may
    legitimately want. ``0s`` is likewise accepted and simply means no delay,
    the same as leaving the field blank.

    The one thing that does bound this in practice is the banner's own window —
    once ``ends_at`` passes, the message stops being served at all.
    """
    if not isinstance(value, str):
        raise ValidationError(DISMISSABLE_AFTER_FORMAT_ERROR)
    match = _DURATION.match(value.strip().lower())
    if not match:
        raise ValidationError(DISMISSABLE_AFTER_FORMAT_ERROR)
    return f"{int(match.group(1))}{match.group(2)}"


def duration_seconds(value: Any) -> Optional[int]:
    """A stored duration as a second count, or None if unreadable.

    The read side's counterpart to `normalize_duration`, tolerating whatever is
    in the column rather than raising — the same rule `decode_list` follows, and
    for the same reason: one hand-edited row must not take down the endpoint.
    """
    if not isinstance(value, str):
        return None
    match = _DURATION.match(value.strip().lower())
    return int(match.group(1)) * _DURATION_UNITS[match.group(2)] if match else None


def _version_error(field: str) -> str:
    return f"Invalid {field}. Expected a dotted numeric version, e.g. '1.2.3'"


def _timestamp_error(field: str) -> str:
    return f"Invalid {field}. Expected an ISO-8601 timestamp"


def _flag_error(field: str) -> str:
    return f"{field} must be true or false"


def _count_error(field: str) -> str:
    return f"{field} must be a non-negative integer"


def _length_error(field: str, limit: int) -> str:
    return f"{field} must be at most {limit} characters"


def _required_text(payload: Dict[str, Any], field: str, limit: int) -> str:
    """A present, non-empty, length-bounded string field."""
    value = payload[field]
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} is required and must be a string")
    value = value.strip()
    if len(value) > limit:
        raise ValidationError(_length_error(field, limit))
    return value


def read_flag(payload: Dict[str, Any], field: str, default: bool) -> bool:
    value = payload.get(field, default)
    if not isinstance(value, bool):
        raise ValidationError(_flag_error(field))
    return value


def _count(payload: Dict[str, Any], field: str) -> Optional[int]:
    value = payload.get(field)
    if value is None:
        return None
    # bool is an int subclass and `min_days_count: true` is a client bug, not a 1.
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValidationError(_count_error(field))
    return value


def _version(payload: Dict[str, Any], field: str) -> Optional[str]:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or versions.parse_version(value.strip()) is None:
        raise ValidationError(_version_error(field))
    return value.strip()


def _timestamp(payload: Dict[str, Any], field: str) -> Optional[str]:
    """A stored-form timestamp, or None. Canonicalised to aware UTC on the way in."""
    value = payload.get(field)
    if value is None:
        return None
    parsed = parse_iso(value)
    if parsed is None:
        raise ValidationError(_timestamp_error(field))
    return parsed.isoformat()


def _string_list(payload: Dict[str, Any], field: str, error: str) -> Optional[List[str]]:
    """A list-valued targeting field, or None when it constrains nothing.

    An empty list means "everyone" exactly as an absent one does, so it is
    normalised away here — one stored representation for one meaning, rather
    than a NULL and a `[]` that a reader in sqlite-web has to know are the same.

    This is the opposite of the rule in ``notifications``, where an empty
    ``days`` is rejected. There, an empty list could only come from a client
    that built it from an empty selection and would create a watch that never
    fires. Here it creates a message that reaches *more* people, which is the
    default anyway — there is no silent-failure mode to protect against.
    """
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValidationError(error)
    if not value:
        return None
    return value


def _client_ids(payload: Dict[str, Any]) -> Optional[List[str]]:
    values = _string_list(payload, "client_ids", CLIENT_IDS_ERROR)
    if values is None:
        return None
    for value in values:
        if not isinstance(value, str):
            raise ValidationError(CLIENT_IDS_ERROR)
        try:
            uuid.UUID(value)
        except ValueError:
            raise ValidationError(f"Invalid client_id in client_ids: {value}")
    return values


def _os(payload: Dict[str, Any]) -> Optional[List[str]]:
    values = _string_list(payload, "os", OS_ERROR)
    if values is None:
        return None

    wanted = set()
    for value in values:
        if not isinstance(value, str):
            raise ValidationError(OS_ERROR)
        name = value.strip().lower()
        if name not in VALID_OS:
            raise ValidationError(OS_ERROR)
        wanted.add(name)
    # Canonical order and deduplicated, so two equivalent selections store the
    # same value and read the same in sqlite-web.
    return [name for name in VALID_OS if name in wanted]


def encode_list(values: Optional[List[str]]) -> Optional[str]:
    """A targeting list in its stored form: JSON, or NULL for "everyone"."""
    return None if values is None else json.dumps(values)


def decode_list(value: Any) -> Optional[List[str]]:
    """Read a stored targeting list back, tolerating whatever is in the column.

    The read side never raises. A hand-edited row in sqlite-web that no longer
    holds valid JSON reads as "no constraint", which serves the message more
    widely than intended but keeps the endpoint up — the alternative is one bad
    row taking down every client's message list.
    """
    if value is None or value == "":
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, list) or not decoded:
        return None
    return [item for item in decoded if isinstance(item, str)] or None


def _targeting_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate the six fields that decide *who* sees a message.

    Shared by a full ``MessageRequest`` and a bare ``AudienceRequest``, so the
    dashboard's live audience count is validated by exactly the rules — and
    rejected with exactly the strings — that the eventual save will apply.
    """
    min_version = _version(payload, "min_version")
    max_version = _version(payload, "max_version")
    if (
        min_version is not None
        and max_version is not None
        and versions.compare(
            versions.parse_version(min_version), versions.parse_version(max_version)
        )
        > 0
    ):
        raise ValidationError(VERSION_ORDER_ERROR)

    min_days_count = _count(payload, "min_days_count")
    max_days_count = _count(payload, "max_days_count")
    if (
        min_days_count is not None
        and max_days_count is not None
        and min_days_count > max_days_count
    ):
        raise ValidationError(DAYS_COUNT_ORDER_ERROR)

    return {
        "client_ids": _client_ids(payload),
        "os": _os(payload),
        "min_version": min_version,
        "max_version": max_version,
        "min_days_count": min_days_count,
        "max_days_count": max_days_count,
    }


@dataclass(frozen=True)
class AudienceRequest:
    """A validated ``POST /admin/audience`` body: targeting rules and nothing else.

    An operator setting up who a message reaches has usually not written the
    copy yet, so this deliberately does not require a title, a body or a
    display — asking for them would make the count useless exactly when it is
    most wanted.
    """

    client_ids: Optional[List[str]] = None
    os: Optional[List[str]] = None
    min_version: Optional[str] = None
    max_version: Optional[str] = None
    min_days_count: Optional[int] = None
    max_days_count: Optional[int] = None

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "AudienceRequest":
        if not isinstance(payload, dict):
            raise ValidationError("Request body is required")
        return cls(**_targeting_fields(payload))


@dataclass(frozen=True)
class Message:
    """One row of ``messages``."""

    message_id: str
    title: str
    body: str
    client_ids: Optional[List[str]]
    os: Optional[List[str]]
    min_version: Optional[str]
    max_version: Optional[str]
    min_days_count: Optional[int]
    max_days_count: Optional[int]
    starts_at: str
    ends_at: Optional[str]
    retain: bool
    expires_at: Optional[str]
    display: str
    level: str
    priority: int
    action_url: Optional[str]
    action_label: Optional[str]
    enabled: bool
    revision: int
    created_at: str
    updated_at: str
    dismissable_at: Optional[str] = None
    dismissable_after: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Message":
        return cls(
            message_id=row["message_id"],
            title=row["title"],
            body=row["body"],
            client_ids=decode_list(row["client_ids"]),
            os=decode_list(row["os"]),
            min_version=row["min_version"],
            max_version=row["max_version"],
            min_days_count=row["min_days_count"],
            max_days_count=row["max_days_count"],
            starts_at=row["starts_at"],
            ends_at=row["ends_at"],
            retain=bool(row["retain"]),
            expires_at=row["expires_at"],
            display=row["display"],
            level=row["level"],
            priority=row["priority"],
            action_url=row["action_url"],
            action_label=row["action_label"],
            enabled=bool(row["enabled"]),
            revision=row["revision"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            dismissable_at=row["dismissable_at"],
            dismissable_after=row["dismissable_after"],
        )

    def to_api(self) -> Dict[str, Any]:
        """The client shape.

        Deliberately omits every targeting column. A client that could see
        ``client_ids`` would learn who else was targeted, and one that could
        see ``enabled`` or ``priority`` would be reading operational state it
        has no use for. ``retain`` and ``expires_at`` are here because the
        client acts on them — they decide how long the message stays in the
        inbox once read.
        """
        return {
            "message_id": self.message_id,
            "revision": self.revision,
            "title": self.title,
            "body": self.body,
            "display": self.display,
            "level": self.level,
            "retain": self.retain,
            "expires_at": self.expires_at,
            "action_url": self.action_url,
            "action_label": self.action_label,
            # Always sent, null on the types that cannot use them, so the client
            # reads one shape rather than branching on `display` to know which
            # keys exist.
            "dismissable_at": self.dismissable_at,
            "dismissable_after": self.dismissable_after,
        }

    def to_admin_api(self) -> Dict[str, Any]:
        """Everything, for the dashboard and the admin CLI."""
        return {
            "message_id": self.message_id,
            "title": self.title,
            "body": self.body,
            "client_ids": self.client_ids,
            "os": self.os,
            "min_version": self.min_version,
            "max_version": self.max_version,
            "min_days_count": self.min_days_count,
            "max_days_count": self.max_days_count,
            "starts_at": self.starts_at,
            "ends_at": self.ends_at,
            "retain": self.retain,
            "expires_at": self.expires_at,
            "dismissable_at": self.dismissable_at,
            "dismissable_after": self.dismissable_after,
            "display": self.display,
            "level": self.level,
            "priority": self.priority,
            "action_url": self.action_url,
            "action_label": self.action_label,
            "enabled": self.enabled,
            "revision": self.revision,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class MessageRequest:
    """A validated ``POST``/``PUT /admin/messages`` body."""

    title: str
    body: str
    display: str
    level: str
    priority: int
    retain: bool
    enabled: bool
    starts_at: str
    ends_at: Optional[str] = None
    expires_at: Optional[str] = None
    client_ids: Optional[List[str]] = None
    os: Optional[List[str]] = None
    min_version: Optional[str] = None
    max_version: Optional[str] = None
    min_days_count: Optional[int] = None
    max_days_count: Optional[int] = None
    action_url: Optional[str] = None
    action_label: Optional[str] = None
    dismissable_at: Optional[str] = None
    dismissable_after: Optional[str] = None

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "MessageRequest":
        if not isinstance(payload, dict):
            raise ValidationError("Request body is required")

        for name in REQUIRED_FIELDS:
            if name not in payload:
                raise ValidationError(f"Missing required field: {name}")

        title = _required_text(payload, "title", MAX_TITLE_LENGTH)
        body = _required_text(payload, "body", MAX_BODY_LENGTH)
        # Parsed for its exceptions, not its value: the tree is what the client
        # builds, and the server only has to know the body can be built into one.
        markdown.parse(body)

        display = payload["display"]
        if display not in VALID_DISPLAYS:
            raise ValidationError(DISPLAY_ERROR)

        level = payload.get("level", LEVEL_INFO)
        if level not in VALID_LEVELS:
            raise ValidationError(LEVEL_ERROR)

        priority = payload.get("priority", 0)
        if not isinstance(priority, int) or isinstance(priority, bool):
            raise ValidationError(PRIORITY_ERROR)

        retain = read_flag(payload, "retain", False)
        enabled = read_flag(payload, "enabled", True)

        targeting = _targeting_fields(payload)

        # An omitted starts_at means "live now", which is what an operator who
        # did not reach for the field meant. The column is NOT NULL, so there
        # is no representation of "unset" to defer the decision to.
        starts_at = _timestamp(payload, "starts_at") or utc_now_iso()
        ends_at = _timestamp(payload, "ends_at")
        expires_at = _timestamp(payload, "expires_at")

        if ends_at is not None and not parse_iso(starts_at) < parse_iso(ends_at):
            raise ValidationError(STARTS_BEFORE_ENDS_ERROR)

        # `expires_at` was once required to be at or after `ends_at`, on the
        # reasoning that a message cannot leave the inbox before it has stopped
        # being served. That is backwards. `expires_at` is a client-side
        # instruction carried in the payload, and the only way to revise it is
        # to keep delivering the message — so "expire this from every inbox
        # now" is precisely an `expires_at` brought back inside the delivery
        # window, which the old rule forbade. See `targeting.matches`.

        # Rejected rather than ignored on a modal or an inbox entry, the same
        # way notifications rejects a day filter on a type that names one
        # session. A silently dropped delay is a delay the operator believes is
        # in force, and the only way to find out otherwise is a user dismissing
        # something they were not meant to be able to.
        if display != DISPLAY_BANNER:
            for name in DISMISSAL_FIELDS:
                if payload.get(name) is not None:
                    raise ValidationError(_dismissal_only_error(name))

        dismissable_at = _timestamp(payload, "dismissable_at")
        dismissable_after = (
            None
            if payload.get("dismissable_after") is None
            else normalize_duration(payload["dismissable_after"])
        )
        # The rule the operator asked for: a banner cannot still be locked once
        # it has expired out of existence. Only checkable when both are set —
        # a null expires_at is "never", which nothing can be later than.
        if (
            dismissable_at is not None
            and expires_at is not None
            and parse_iso(dismissable_at) > parse_iso(expires_at)
        ):
            raise ValidationError(DISMISSABLE_AT_AFTER_EXPIRES_ERROR)

        action_url = payload.get("action_url")
        action_label = payload.get("action_label")
        if (action_url is None) != (action_label is None):
            raise ValidationError(ACTION_PAIR_ERROR)
        if action_url is not None:
            if not isinstance(action_url, str) or not action_url.startswith(URL_SCHEME):
                raise ValidationError(ACTION_URL_SCHEME_ERROR)
            action_label = _required_text(
                payload, "action_label", MAX_ACTION_LABEL_LENGTH
            )
            action_url = action_url.strip()

        return cls(
            title=title,
            body=body,
            display=display,
            level=level,
            priority=priority,
            retain=retain,
            enabled=enabled,
            starts_at=starts_at,
            ends_at=ends_at,
            expires_at=expires_at,
            action_url=action_url,
            action_label=action_label,
            dismissable_at=dismissable_at,
            dismissable_after=dismissable_after,
            **targeting,
        )
