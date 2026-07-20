---
name: wave-dashboard
description: Modify and extend The Wave dashboard (Flask + Chart.js over SQLite). Use when adding a chart, table, column, or new data source to the dashboard in this repo, or changing how its queries/UI work.
---

# Extending The Wave dashboard

This repo is a Flask + Chart.js dashboard that reads two **read-only** SQLite databases and renders
charts, configurable detail tables, and a notifications list. Use this skill when asked to add or
change charts, tables, columns, or data sources.

## Architecture (where things live)

```
src/
├── main.py       # Flask app: HTML page route + /api/* JSON endpoints
├── config.py     # DB path settings (env-overridable, with on-disk defaults)
├── db.py         # read-only sqlite connection helpers (+ ATTACH for cross-db joins)
├── queries.py    # ALL SQL lives here, returned as plain dicts/lists
├── templates/    # base.html (shell + Chart.js CDN), index.html (page content)
└── static/       # dashboard.js (fetch + render), style.css
```

Data flow: `dashboard.js` fetches an `/api/...` endpoint → the route in `main.py` calls a function
in `queries.py` → that function opens a read-only connection via `db.py`. Keep SQL in `queries.py`,
keep rendering in `dashboard.js`.

Run / verify locally after any change:
```bash
uv run flask --app src.main run --port 5002      # then open http://localhost:5002/
curl -s 'localhost:5002/api/clients/new?granularity=day' | head
```

## Key facts about the data

- `clients.first_seen` / `clients.last_seen` are ISO-8601 **with timezone**. SQLite `date()` /
  `strftime()` normalise these to UTC — group with `date(col)`, `strftime('%Y-W%W', col)`,
  `strftime('%Y-%m', col)` (see `_GRANULARITY` in `queries.py`).
- "Active clients" (`last_seen`) is approximate — each client only contributes to its most recent
  day. Keep the approximate label if you touch that chart.
- Aliases come from `water_temperature.clients.alias`, joined on `notifications.client_id = uuid`.
  Many clients have no alias (NULL) — that's expected, render a placeholder.
- `notifications.thresholds` / `notified_thresholds` are JSON arrays stored as TEXT — parse with
  `_parse_json_list` in `queries.py`.

## Active-clients chart: end-of-day snapshots

`last_seen` only stores each client's *most recent* day, so a live "active per day" count is wrong
for past days. Fix: a nightly snapshot freezes each completed day's active set.

- **Store** (`config.DAILY_ACTIVE_DB_PATH`, default `data/daily_active.db`, dashboard-owned,
  writable, git-ignored): `daily_active(date, client_id, is_cloud)` + `snapshot_runs(date,
  computed_at, client_count)`. Per-client rows (not a bare count) so week/month stay correct
  (distinct users) and the cloud toggle works on history.
- **Snapshot script** `scripts/snapshot_active.py` records, for a UTC day, every client whose
  `last_seen` is that day (with `is_cloud` via reverse DNS). Idempotent per date; `--date` for
  manual/backfill. Run by cron just before **UTC** midnight (`CRON_TZ=UTC 55 23`), via
  `uv run --directory <repo> python scripts/snapshot_active.py`.
- **Merge** in `queries.active_clients_by_period` (used by `/api/clients/active`): `db.upstream(
  attach_active=True)` attaches the store as `hist`; a UNION counts DISTINCT clients per period from
  hist (completed days, `date < date('now')`) + live `clients` (today, or any day not in
  `snapshot_runs` → fallback). Falls back to pure live (`clients_by_period`) when the store is absent
  or granularity is `hour` (snapshots are daily). The "new clients" chart is unaffected (`first_seen`
  is immutable).

Note: history accuracy accrues from when the cron starts; pre-existing days stay approximate.

## Recipe: add a new chart

1. **Query** — add a function in `src/queries.py` returning `[{"period": str, "count": int}, ...]`
   (or whatever shape your chart needs). Reuse `_period_expr` / `_date_range_where` for time series.
2. **Endpoint** — add a route in `src/main.py`, e.g. `@app.route("/api/<thing>")`, reading
   `request.args` (`granularity`, `from`, `to`) and returning `jsonify(...)`.
3. **Canvas** — add `<canvas id="chart-<thing>"></canvas>` inside a `.chart-card` in
   `src/templates/index.html`.
4. **Render** — in `src/static/dashboard.js`, fetch the endpoint in `refreshCharts()` and call
   `renderBarChart("chart-<thing>", label, data, color)` (or add a new Chart.js type).

## The clients table is paginated

There is one clients table (id `table-clients`). The date range filters it on `last_seen`
("active in window"); the two time-series charts are separate (`/api/clients/new` by `first_seen`,
`/api/clients/active` by `last_seen`).

`/api/clients` returns `{"total": N, "rows": [...]}` — `total` is the unpaginated count for the
current filter, `rows` is one page. Params: `sort`, `dir`, `limit` (page size; `0` ⇒ all rows, no
pagination), `offset` (start row). The frontend state lives in the single `clientTable` object
(`sort`, `dir`, `hidden`, `offset`); `renderClientTable()`, `updatePager()`, and `wirePager()`
drive the table, the "start–end of total" status, and the Prev/Next buttons. Changing filters,
limit, or sort resets `offset` to 0. If you change the response shape, update `renderClientTable`
which destructures `{ total, rows }`.

## Hiding Google/Apple clients

Many "clients" are Google/Apple fetchers (Global Cache, proxies, crawlers), not real users.
`src/cloud_ips.py` provides `is_cloud_ip(ip)`, registered by `db.py` as a SQLite scalar function so
filtering happens in SQL (keeps pagination/counts correct). Detection is **reverse-DNS-first**: an
IP whose PTR ends in a Google domain (`google.com` / `1e100.net` / `googleusercontent.com`, matched
on a dotted boundary) is Google — this catches Global Cache nodes embedded in ISP IP space that no
published CIDR covers. The CIDR lists (`GOOGLE_RANGES`, `APPLE_RANGES`) are a fast-path (skip DNS for
known ranges) and a fallback if DNS is down; Apple is CIDR-only (17.0.0.0/8). AWS is intentionally
NOT classified.

Reverse lookups are cached and prewarmed concurrently (`cloud_ips.prewarm`, called by
`queries._prewarm_cloud` whenever `exclude_cloud` is set) with a ~4s batch deadline, so the first
toggle stays fast even with no-PTR IPs. The `exclude_cloud` param on `summary_stats` /
`clients_by_period` / `client_rows` adds `NOT (is_cloud_ip(last_ip) OR is_cloud_ip(first_ip))` to the
WHERE (via `_date_range_where`). The "Hide Google/Apple" checkbox (`#exclude-cloud`) applies to the
badges, charts, and table, refreshing immediately on change.

To extend: add PTR suffixes to `_GOOGLE_PTR_SUFFIXES` (e.g. to also flag AWS, add `amazonaws.com`) or
CIDRs to the range lists. Clients with a NULL IP are never matched (kept). **Containerization note:**
this needs outbound DNS from the container.

## Recipe: add a column to a client table

1. In `queries.py` add the column to the `SELECT` in `client_rows`, and to `_CLIENT_SORT_COLUMNS`
   if it should be sortable.
2. In `dashboard.js` add an entry to `CLIENT_COLUMNS` (`{ key, label, default, num? }`). It
   automatically gets a sort header and a show/hide toggle. Add special formatting in `fmtCell` if
   needed.

## Recipe: add a whole new table

Mirror the client-table pattern: a `queries.py` function → an `/api/...` route → a `<table>` in
`index.html` → a render function in `dashboard.js` wired into `DOMContentLoaded`. The notifications
table (`renderNotifications` + `NOTIF_COLUMNS`) is the simplest template to copy.

## Recipe: add a new data source (database)

1. Add a `*_DB_PATH` setting in `src/config.py` (env-overridable, with an on-disk default).
2. In `src/db.py` add a read-only connection helper using `_connect_ro(path)`. If the new data must
   be **joined** to an existing DB in one query, ATTACH it on an existing connection (see how
   `upstream(attach_notifications=True)` ATTACHes notifications.db as schema `notif`).
3. Query it from `queries.py` as usual.
4. Remember: connections are always opened `mode=ro`; never write to source DBs.

## Conventions to keep

- All SQL goes in `queries.py`; validate any user-supplied sort/column against an allow-list before
  interpolating it into SQL (everything else uses bound `?` params).
- Endpoints return plain JSON; the frontend owns all formatting.
- This is its own git repo — keep everything self-contained here.
