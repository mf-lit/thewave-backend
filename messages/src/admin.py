"""Maintenance CLI.

    docker exec thewave-messages-api uv run python -m src.admin <command>

Talks to the repository directly rather than over HTTP, deliberately. This is
the recovery path for when the dashboard, the admin key, or the admin routes
themselves are the thing that is broken — which is exactly when a message
needs retracting.
"""
from __future__ import annotations

import argparse
import sys
from typing import List

from .models import Message
from .services import Services
from .settings import configure_logging
from .targeting import audience_matches


def _window(message: Message) -> str:
    """The live window, as an operator reads it: a start, and an end or none."""
    return f"{message.starts_at[:16]} → {message.ends_at[:16] if message.ends_at else 'open'}"


def _print_table(messages: List[Message], acks: dict) -> None:
    if not messages:
        print("No messages.")
        return

    headers = ("ID", "ON", "DISPLAY", "LEVEL", "PRI", "REV", "ACKS", "WINDOW", "TITLE")
    rows = [
        (
            message.message_id,
            "yes" if message.enabled else "NO",
            message.display,
            message.level,
            str(message.priority),
            str(message.revision),
            str(acks.get(message.message_id, 0)),
            _window(message),
            message.title,
        )
        for message in messages
    ]

    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))
    ]
    line = "  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))
    print(line)
    print("-" * len(line))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def _get_or_report(services: Services, message_id: str) -> Message:
    message = services.messages.get(message_id)
    if message is None:
        print(f"No message with id {message_id}.")
        raise SystemExit(1)
    return message


def cmd_list(services: Services, args) -> int:
    _print_table(services.messages.list_all(), services.messages.ack_counts())
    return 0


def cmd_show(services: Services, args) -> int:
    message = _get_or_report(services, args.message_id)
    fields = message.to_admin_api()
    width = max(len(name) for name in fields)
    for name, value in fields.items():
        print(f"{name.rjust(width)}  {value}")
    print(f"{'acks'.rjust(width)}  {services.messages.ack_counts().get(message.message_id, 0)}")
    return 0


def cmd_enable(services: Services, args) -> int:
    return _set_enabled(services, args.message_id, True)


def cmd_disable(services: Services, args) -> int:
    return _set_enabled(services, args.message_id, False)


def _set_enabled(services: Services, message_id: str, enabled: bool) -> int:
    _get_or_report(services, message_id)
    services.messages.set_enabled(message_id, enabled)
    print(f"Message {message_id} is now {'enabled' if enabled else 'disabled'}.")
    return 0


def cmd_delete(services: Services, args) -> int:
    message = _get_or_report(services, args.message_id)
    services.messages.delete(args.message_id)
    print(f"Deleted {args.message_id} ({message.title}) and its acks.")
    return 0


def cmd_audience(services: Services, args) -> int:
    """Who a stored message's rules currently reach.

    The same ``audience_matches`` the API and the dashboard call, so this
    cannot disagree with what either of them reports.
    """
    message = _get_or_report(services, args.message_id)
    clients = services.directory.iter_clients()
    if not clients:
        print("No client directory available; cannot count an audience.")
        return 1

    matched = [client.client_id for client in clients if audience_matches(message, client)]
    print(f"{len(matched)} of {len(clients)} client(s) match {args.message_id}.")
    for client_id in matched[:10]:
        print(f"  {client_id}")
    if len(matched) > 10:
        print(f"  … and {len(matched) - 10} more")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.admin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="show every message with its ack count")
    listing.set_defaults(handler=cmd_list)

    for name, handler, help_text in (
        ("show", cmd_show, "print every field of one message"),
        ("enable", cmd_enable, "serve a message again"),
        ("disable", cmd_disable, "retract a message without deleting it"),
        ("delete", cmd_delete, "delete a message and its acks"),
        ("audience", cmd_audience, "count the clients a message's rules match"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("message_id")
        command.set_defaults(handler=handler)

    return parser


def main(argv=None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    services = Services.build()
    return args.handler(services, args)


if __name__ == "__main__":
    sys.exit(main())
