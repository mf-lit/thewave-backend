"""HTTP client for the messages service's admin API.

Over HTTP deliberately, and not by opening ``messages.db``. The dashboard's
read-only-database invariant is documented in ``README.md``, ``src/db.py`` and
the compose file, and a write connection here would be the first exception to
it. The admin key comes from the environment and never reaches the browser —
the page talks to this app, and this app talks to the service.

This module also owns the one timezone boundary in the dashboard. The operator
thinks in Europe/London; the service stores UTC and nothing else. So:

* on the way **out**, a naive wall-clock string from a ``datetime-local`` input
  is read as London and converted to UTC;
* on the way **in**, each timestamp gains a ``*_local`` sibling rendered in
  London, so the page prints strings rather than doing timezone arithmetic in
  JavaScript.

Note the deliberate difference from the messages service's own rule, where a
naive timestamp means UTC. That rule is right for an API; this one is right for
a form an operator fills in. The conversion happens here precisely so the two
never meet.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

import requests

from . import config

LONDON = ZoneInfo("Europe/London")

# The timestamps an operator types, sent as London wall-clock.
FORM_FIELDS = ("starts_at", "ends_at", "expires_at")

# Every timestamp the list displays, London-rendered for reading.
DISPLAY_FIELDS = FORM_FIELDS + ("created_at", "updated_at")

TIMEOUT_SECONDS = 10


class MessagesApiError(RuntimeError):
    """A failure to relay to the browser, carrying the status to relay it with."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.message = message
        self.status = status


def london_to_utc(value):
    """A naive London wall-clock string as a UTC ISO-8601 timestamp.

    A value that already carries an offset passes through untouched, so a
    payload round-tripped through the list does not shift by an hour.

    During the autumn clock change one wall-clock hour happens twice; Python's
    default ``fold=0`` picks the first (BST) occurrence. Nothing here is
    scheduled to the second, and the alternative is asking an operator to
    disambiguate an hour they did not know was ambiguous.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        # Not our business to reject: hand it to the service, whose validation
        # error is the one the operator should see.
        return value
    if parsed.tzinfo is not None:
        return parsed.isoformat()
    return parsed.replace(tzinfo=LONDON).astimezone(ZoneInfo("UTC")).isoformat()


def utc_to_london(value):
    """A stored UTC timestamp as ``YYYY-MM-DD HH:MM`` in London, or None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return value
    return parsed.astimezone(LONDON).strftime("%Y-%m-%d %H:%M")


def to_utc_payload(payload: dict) -> dict:
    """A compose-form body with its timestamps converted out of London."""
    converted = dict(payload)
    for field in FORM_FIELDS:
        if field in converted:
            converted[field] = london_to_utc(converted[field])
    return converted


def with_local_times(item: dict) -> dict:
    """A message with a ``*_local`` sibling for each timestamp it carries."""
    annotated = dict(item)
    for field in DISPLAY_FIELDS:
        annotated[f"{field}_local"] = utc_to_london(item.get(field))
    return annotated


class MessagesApi:
    """Thin wrapper over the admin endpoints. One method per endpoint."""

    def __init__(self, base_url: str = None, admin_key: str = None):
        self.base_url = (base_url or config.MESSAGES_API_URL).rstrip("/")
        self.admin_key = admin_key if admin_key is not None else config.MESSAGES_ADMIN_KEY
        self._session = requests.Session()

    def _call(self, method: str, path: str, payload=None):
        if not self.admin_key:
            raise MessagesApiError(
                "MESSAGES_ADMIN_KEY is not set; the dashboard cannot reach the "
                "messages admin API.",
                503,
            )

        try:
            response = self._session.request(
                method,
                f"{self.base_url}{path}",
                json=payload,
                headers={"x-admin-key": self.admin_key},
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise MessagesApiError(f"Could not reach the messages service: {exc}", 502)

        if response.status_code >= 400:
            # Relay the service's own message. Its validation strings are
            # written for the person composing the message, and rewording them
            # here would put a second vocabulary in front of the same rules.
            try:
                message = response.json().get("error", response.text)
            except ValueError:
                message = response.text or f"HTTP {response.status_code}"
            raise MessagesApiError(message, response.status_code)

        return response.json() if response.content else {}

    def list_messages(self):
        payload = self._call("GET", "/admin/messages")
        return [with_local_times(item) for item in payload.get("messages", [])]

    def get(self, message_id: str):
        return with_local_times(self._call("GET", f"/admin/messages/{message_id}"))

    def create(self, payload: dict):
        return with_local_times(
            self._call("POST", "/admin/messages", to_utc_payload(payload))
        )

    def update(self, message_id: str, payload: dict):
        return with_local_times(
            self._call("PUT", f"/admin/messages/{message_id}", to_utc_payload(payload))
        )

    def delete(self, message_id: str):
        return self._call("DELETE", f"/admin/messages/{message_id}")

    def set_enabled(self, message_id: str, enabled: bool):
        return with_local_times(
            self._call(
                "POST", f"/admin/messages/{message_id}/enabled", {"enabled": enabled}
            )
        )

    def audience(self, payload: dict):
        return self._call("POST", "/admin/audience", payload)

    def preview(self, body: str):
        """The server's parse tree for a draft body.

        The page renders this rather than parsing markdown itself — one
        implementation of the grammar, and it is the one that decides whether
        a save succeeds.
        """
        return self._call("POST", "/admin/preview", {"body": body})
