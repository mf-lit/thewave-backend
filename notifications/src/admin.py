"""Maintenance CLI.

    docker exec thewave-notifications-worker uv run python -m src.admin <command>

Replaces the old ``scripts/`` directory, every file of which still talked to
the DynamoDB backend this service stopped using.
"""
from __future__ import annotations

import argparse
import sys
from typing import List

from .models import Notification
from .services import Services
from .settings import configure_logging


def _print_table(notifications: List[Notification], tokens: set) -> None:
    if not notifications:
        print("No notifications.")
        return

    headers = ("DATE", "TIME", "SIDE", "TYPE", "AVAIL", "THRESHOLDS", "TITLE", "CLIENT", "PUSH")
    rows = [
        (
            n.date,
            n.time,
            n.side,
            n.notification_type,
            "-" if n.last_checked_availability is None else str(n.last_checked_availability),
            ",".join(str(t) for t in (n.thresholds or [])) or "-",
            n.title,
            n.client_id,
            "yes" if n.client_id in tokens else "NO TOKEN",
        )
        for n in notifications
    ]

    widths = [max(len(str(cell)) for cell in column) for column in zip(headers, *rows)]
    line = "  ".join(f"{h:<{w}}" for h, w in zip(headers, widths))
    print(line)
    print("-" * len(line))
    for row in rows:
        print("  ".join(f"{cell:<{w}}" for cell, w in zip(row, widths)))
    print(f"\n{len(notifications)} notification(s).")


def cmd_list(services: Services, _args) -> int:
    notifications = services.notifications.all()
    tokens = services.clients.existing_ids({n.client_id for n in notifications})
    _print_table(notifications, tokens)
    print(f"{services.clients.count()} client token(s) stored.")
    return 0


def cmd_delete_client(services: Services, args) -> int:
    deleted = services.notifications.delete_for_client(args.client_id)
    print(f"Deleted {deleted} notification(s) for client {args.client_id}.")
    if args.token:
        removed = services.clients.delete_token(args.client_id)
        print("Deleted FCM token." if removed else "No FCM token stored.")
    return 0


def cmd_clear_thresholds(services: Services, _args) -> int:
    """Re-arm every below_threshold notification so it can fire again."""
    count = services.notifications.clear_notified_thresholds()
    print(f"Cleared notified_thresholds on {count} notification(s).")
    return 0


def cmd_prune_tokenless(services: Services, args) -> int:
    """Drop notifications that can never be delivered, and dead client rows."""
    notifications = services.notifications.all()
    with_tokens = services.clients.existing_ids({n.client_id for n in notifications})
    orphaned = [n for n in notifications if n.client_id not in with_tokens]
    blank = services.clients.ids_with_blank_tokens()

    for notification in orphaned:
        print(
            f"  notification {notification.notification_id} "
            f"({notification.date} {notification.time} {notification.side}) "
            f"client {notification.client_id}"
        )
    for client_id in blank:
        print(f"  client {client_id} has an empty token")

    if args.dry_run:
        print(
            f"\nDry run: would delete {len(orphaned)} notification(s) "
            f"and {len(blank)} client row(s)."
        )
        return 0

    for notification in orphaned:
        services.notifications.delete(notification.client_id, notification.notification_id)
    for client_id in blank:
        services.clients.delete_token(client_id)

    print(f"\nDeleted {len(orphaned)} notification(s) and {len(blank)} client row(s).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m src.admin", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="show every notification and whether it can be delivered")
    listing.set_defaults(handler=cmd_list)

    delete = sub.add_parser("delete-client", help="delete one client's notifications")
    delete.add_argument("client_id")
    delete.add_argument("--token", action="store_true", help="also delete their FCM token")
    delete.set_defaults(handler=cmd_delete_client)

    clear = sub.add_parser("clear-thresholds", help="re-arm all below_threshold notifications")
    clear.set_defaults(handler=cmd_clear_thresholds)

    prune = sub.add_parser("prune-tokenless", help="delete undeliverable notifications")
    prune.add_argument("--dry-run", action="store_true", help="report without deleting")
    prune.set_defaults(handler=cmd_prune_tokenless)

    return parser


def main(argv=None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    services = Services.build()
    return args.handler(services, args)


if __name__ == "__main__":
    sys.exit(main())
