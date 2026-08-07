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


def _fires_at(notification: Notification) -> str:
    """The seat counts that trigger this row, with the sense of the test.

    below_threshold fires at or below its thresholds and quiet_session at or
    above its minimum, so the bare numbers would read as each other's opposite
    sitting in one column.
    """
    if notification.thresholds:
        return "<=" + ",".join(str(t) for t in notification.thresholds)
    if notification.minimum_slots is not None:
        return f">={notification.minimum_slots}"
    return "-"


def _when(notification: Notification) -> str:
    """The day and time-of-day filter on a rolling watch, or ``-`` for none.

    A filtered any_quiet_session skips sessions silently, by design, so this
    column is the only place an operator can see why a watch that otherwise
    looks right is not firing.

    An unreadable ``days`` column is called out rather than rendered as no
    filter: it means the worker is refusing to scan that row at all, which
    looks identical to "nothing was quiet" from the outside.
    """
    if notification.days == []:
        return "BAD DAYS"

    parts = []
    if notification.days:
        parts.append(",".join(notification.days))
    before, after = notification.not_before, notification.not_after
    if before and after:
        parts.append(f"{before}-{after}")
    elif before:
        parts.append(f"from {before}")
    elif after:
        parts.append(f"to {after}")
    return " ".join(parts) or "-"


def _print_table(notifications: List[Notification], tokens: set) -> None:
    if not notifications:
        print("No notifications.")
        return

    headers = (
        "DATE", "TIME", "SIDE", "TYPE", "AVAIL", "FIRES AT", "WHEN", "TITLE", "CLIENT", "PUSH"
    )
    rows = [
        (
            # An any_quiet_session row watches a window rather than a session,
            # so it stores neither; show its horizon in place of a start time.
            n.date or "-",
            n.time or n.time_before or "-",
            n.side,
            n.notification_type,
            "-" if n.last_checked_availability is None else str(n.last_checked_availability),
            _fires_at(n),
            _when(n),
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


def cmd_clear_notified(services: Services, _args) -> int:
    """Re-arm every any_quiet_session so it can fire for its sessions again."""
    count = services.notifications.clear_notified_performances()
    print(f"Cleared notified_performances on {count} notification(s).")
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

    clear_notified = sub.add_parser(
        "clear-notified", help="re-arm all any_quiet_session notifications"
    )
    clear_notified.set_defaults(handler=cmd_clear_notified)

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
