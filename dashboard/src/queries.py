"""SQL aggregation and listing functions for the dashboard.

Timestamps in the `clients` table are ISO-8601 with a timezone offset; SQLite's
date/strftime functions normalise these to UTC, so grouping is correct.
"""

import json

from . import cloud_ips, db

# --- granularity -> SQL expression over a timestamp column -------------------
# hour:  YYYY-MM-DD HH:00  (UTC, since timestamps carry a tz offset)
# day:   YYYY-MM-DD
# week:  YYYY-Www  (ISO-ish year + week number)
# month: YYYY-MM
_GRANULARITY = {
    "hour": "strftime('%Y-%m-%d %H:00', {col})",
    "day": "date({col})",
    "week": "strftime('%Y-W%W', {col})",
    "month": "strftime('%Y-%m', {col})",
}

# Columns the detail table may sort by (allow-list guards against SQL injection).
_CLIENT_SORT_COLUMNS = {
    "uuid",
    "first_seen",
    "last_seen",
    "request_count",
    "days_count",
    "alias",
    "client_os",
    "client_version",
    "first_ip",
    "last_ip",
}


def _period_expr(column: str, granularity: str) -> str:
    template = _GRANULARITY.get(granularity, _GRANULARITY["day"])
    return template.format(col=column)


def summary_stats(exclude_cloud=False):
    """Headline counts for the top-of-page badges (UTC dates, like the charts).

    - new_this_week: clients first seen in the last 7 days (today + 6 prior)
    - new_yesterday / new_today: clients first seen on those days
    - all_today: clients active today (last_seen today), new or returning
    - active_clients: clients seen in the last 30 days that have returned
      (days_count > 1); ALWAYS excludes Google/Apple, regardless of the toggle

    ``exclude_cloud`` drops known Google/Apple IPs from the first four badges so
    they match the Hide Google/Apple toggle.
    """
    no_cloud = " AND NOT (is_cloud_ip(last_ip) OR is_cloud_ip(first_ip))"
    cloud = no_cloud if exclude_cloud else ""
    with db.upstream() as conn:
        _prewarm_cloud(conn)  # active_clients always classifies IPs
        d_today, d_yesterday, d_weekstart = conn.execute(
            "SELECT date('now'), date('now','-1 day'), date('now','-6 days')"
        ).fetchone()

        def count(col, day_expr):
            return conn.execute(
                f"SELECT COUNT(*) FROM clients WHERE date({col}) {day_expr}{cloud}"
            ).fetchone()[0]
        stats = {
            "new_this_week": count("first_seen", ">= date('now','-6 days')"),
            "new_yesterday": count("first_seen", "= date('now','-1 day')"),
            "new_today": count("first_seen", "= date('now')"),
            "all_today": count("last_seen", "= date('now')"),
            "active_clients": conn.execute(
                "SELECT COUNT(*) FROM clients "
                f"WHERE date(last_seen) >= date('now','-30 days') AND days_count > 1{no_cloud}"
            ).fetchone()[0],
        }

    # "All clients" over past periods needs the snapshot merge (last_seen alone
    # under-counts completed days); honours the exclude_cloud toggle like all_today.
    stats["all_yesterday"] = active_clients_total(d_yesterday, d_yesterday, exclude_cloud)
    stats["all_week"] = active_clients_total(d_weekstart, d_today, exclude_cloud)
    return stats


def clients_by_period(timestamp_column: str, granularity: str, date_from=None, date_to=None, exclude_cloud=False):
    """Count clients grouped by period of ``timestamp_column``.

    ``timestamp_column`` is "first_seen" (new clients) or "last_seen" (active).
    ``exclude_cloud`` drops known Google/Apple IPs.
    Returns ``[{"period": str, "count": int}, ...]`` ordered by period.
    """
    if timestamp_column not in ("first_seen", "last_seen"):
        raise ValueError(f"invalid timestamp column: {timestamp_column}")

    period = _period_expr(timestamp_column, granularity)
    where, params = _date_range_where(timestamp_column, date_from, date_to, exclude_cloud)
    sql = (
        f"SELECT {period} AS period, COUNT(*) AS count "
        f"FROM clients {where} "
        f"GROUP BY period ORDER BY period"
    )
    with db.upstream() as conn:
        if exclude_cloud:
            _prewarm_cloud(conn)
        rows = conn.execute(sql, params).fetchall()
    return [{"period": r["period"], "count": r["count"]} for r in rows]


def active_clients_by_period(granularity, date_from=None, date_to=None, exclude_cloud=False):
    """Active clients per period, fixing the last_seen "only most-recent-day" flaw.

    Uses authoritative end-of-day snapshots (``hist.daily_active``) for COMPLETED
    days, and a live count for today — plus a live fallback for any past day not
    yet snapshotted. Counts DISTINCT clients per period so day/week/month are all
    correct. Falls back entirely to the old live behaviour when no snapshot store
    exists yet, or for hour granularity (snapshots are daily).
    """
    if granularity == "hour" or not db.active_store_exists():
        return clients_by_period("last_seen", granularity, date_from, date_to, exclude_cloud)

    period = _period_expr("d", granularity)
    hist_cloud = " AND is_cloud = 0" if exclude_cloud else ""
    live_cloud = " AND NOT (is_cloud_ip(last_ip) OR is_cloud_ip(first_ip))" if exclude_cloud else ""

    # Completed, snapshotted days come from history; today + any un-snapshotted
    # day comes from live (so it degrades to current behaviour for old history).
    hist_where, hist_params = ["date < date('now')"], []
    live_where = ["(date(last_seen) = date('now') "
                  "OR date(last_seen) NOT IN (SELECT date FROM hist.snapshot_runs))"]
    live_params = []
    if date_from:
        hist_where.append("date >= ?"); hist_params.append(date_from)
        live_where.append("date(last_seen) >= ?"); live_params.append(date_from)
    if date_to:
        hist_where.append("date <= ?"); hist_params.append(date_to)
        live_where.append("date(last_seen) <= ?"); live_params.append(date_to)

    sql = (
        f"SELECT {period} AS period, COUNT(DISTINCT client_id) AS count FROM ("
        f"  SELECT client_id, date AS d FROM hist.daily_active "
        f"  WHERE {' AND '.join(hist_where)}{hist_cloud} "
        f"  UNION "
        f"  SELECT uuid AS client_id, date(last_seen) AS d FROM clients "
        f"  WHERE {' AND '.join(live_where)}{live_cloud} "
        f") GROUP BY period ORDER BY period"
    )
    with db.upstream(attach_active=True) as conn:
        if exclude_cloud:
            _prewarm_cloud(conn)
        rows = conn.execute(sql, hist_params + live_params).fetchall()
    return [{"period": r["period"], "count": r["count"]} for r in rows]


def active_clients_total(date_from, date_to, exclude_cloud=False):
    """Distinct active clients over [date_from, date_to] (inclusive UTC dates).

    Uses end-of-day snapshots for completed days + live for today / un-snapshotted
    days, counting DISTINCT clients across the whole range (not a sum of days).
    Falls back to pure live when no snapshot store exists.
    """
    if not db.active_store_exists():
        cloud = " AND NOT (is_cloud_ip(last_ip) OR is_cloud_ip(first_ip))" if exclude_cloud else ""
        sql = ("SELECT COUNT(DISTINCT uuid) FROM clients "
               f"WHERE date(last_seen) >= ? AND date(last_seen) <= ?{cloud}")
        with db.upstream() as conn:
            if exclude_cloud:
                _prewarm_cloud(conn)
            return conn.execute(sql, (date_from, date_to)).fetchone()[0]

    hist_cloud = " AND is_cloud = 0" if exclude_cloud else ""
    live_cloud = " AND NOT (is_cloud_ip(last_ip) OR is_cloud_ip(first_ip))" if exclude_cloud else ""
    sql = (
        "SELECT COUNT(DISTINCT client_id) FROM ("
        "  SELECT client_id FROM hist.daily_active "
        f"  WHERE date < date('now') AND date >= ? AND date <= ?{hist_cloud} "
        "  UNION "
        "  SELECT uuid AS client_id FROM clients "
        "  WHERE date(last_seen) >= ? AND date(last_seen) <= ? "
        "    AND (date(last_seen) = date('now') "
        "         OR date(last_seen) NOT IN (SELECT date FROM hist.snapshot_runs))"
        f"{live_cloud}"
        ")"
    )
    with db.upstream(attach_active=True) as conn:
        if exclude_cloud:
            _prewarm_cloud(conn)
        return conn.execute(sql, (date_from, date_to, date_from, date_to)).fetchone()[0]


def client_rows(timestamp_column: str, sort="last_seen", direction="desc", date_from=None, date_to=None, limit=40, offset=0, exclude_cloud=False):
    """A page of client detail rows, filtered by ``timestamp_column`` date range.

    ``sort`` is validated against an allow-list; ``direction`` is asc/desc.
    ``limit`` is the page size (falsy/non-positive ⇒ all rows on one page) and
    ``offset`` is the starting row. ``exclude_cloud`` drops known Google/Apple
    IPs. Returns ``{"total": int, "rows": [...]}`` where ``total`` is the
    unpaginated count for the same filter.
    """
    if timestamp_column not in ("first_seen", "last_seen"):
        raise ValueError(f"invalid timestamp column: {timestamp_column}")
    if sort not in _CLIENT_SORT_COLUMNS:
        sort = timestamp_column
    direction = "ASC" if str(direction).lower() == "asc" else "DESC"

    where, params = _date_range_where(timestamp_column, date_from, date_to, exclude_cloud)

    with db.upstream() as conn:
        if exclude_cloud:
            _prewarm_cloud(conn)
        total = conn.execute(f"SELECT COUNT(*) FROM clients {where}", params).fetchone()[0]

        sql = (
            "SELECT uuid, first_seen, last_seen, request_count, days_count, "
            "alias, client_os, client_version, first_ip, last_ip "
            f"FROM clients {where} "
            f"ORDER BY {sort} {direction}"
        )
        page_params = list(params)
        if limit and limit > 0:
            sql += " LIMIT ? OFFSET ?"
            page_params += [limit, max(0, offset)]
        rows = conn.execute(sql, page_params).fetchall()

    return {"total": total, "rows": [dict(r) for r in rows]}


def notifications_with_alias():
    """All notifications, joined to the client's alias from water_temperature.clients.

    Aliases are matched on notifications.client_id = clients.uuid. `thresholds`
    and `notified_thresholds` are parsed from their JSON-text storage.
    """
    sql = (
        "SELECT n.client_id, n.notification_id, n.performance_ak, n.title, "
        "n.date, n.time, n.side, n.notification_type, n.thresholds, "
        "n.notified_thresholds, n.last_checked_availability, n.created_at, "
        "c.alias AS alias "
        "FROM notif.notifications n "
        "LEFT JOIN clients c ON c.uuid = n.client_id "
        "ORDER BY n.date, n.time"
    )
    with db.upstream(attach_notifications=True) as conn:
        rows = conn.execute(sql).fetchall()

    result = []
    for r in rows:
        row = dict(r)
        row["thresholds"] = _parse_json_list(row.get("thresholds"))
        row["notified_thresholds"] = _parse_json_list(row.get("notified_thresholds"))
        result.append(row)
    return result


# --- helpers -----------------------------------------------------------------

def _prewarm_cloud(conn):
    """Resolve all distinct client IPs concurrently so is_cloud_ip hits cache."""
    ips = [r[0] for r in conn.execute(
        "SELECT first_ip FROM clients UNION SELECT last_ip FROM clients"
    ) if r[0]]
    cloud_ips.prewarm(ips)


def _date_range_where(column: str, date_from, date_to, exclude_cloud=False):
    """Build a WHERE clause restricting ``column`` to [date_from, date_to].

    Bounds are inclusive dates (YYYY-MM-DD); the upper bound is extended to the
    end of the day so timestamps on ``date_to`` are included. When
    ``exclude_cloud`` is True, rows whose first/last IP is a known Google/Apple
    address are dropped (via the ``is_cloud_ip`` SQLite function).
    """
    clauses, params = [], []
    if date_from:
        clauses.append(f"{column} >= ?")
        params.append(date_from)
    if date_to:
        clauses.append(f"{column} <= ?")
        params.append(f"{date_to}T23:59:59.999999+00:00")
    if exclude_cloud:
        clauses.append("NOT (is_cloud_ip(last_ip) OR is_cloud_ip(first_ip))")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _parse_json_list(value):
    if value is None or value == "":
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else value
    except (ValueError, TypeError):
        return value
