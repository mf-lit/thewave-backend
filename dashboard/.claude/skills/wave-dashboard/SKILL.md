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
├── main.py          # Flask app: HTML page routes + /api/* and /admin-api/* JSON endpoints
├── config.py        # DB path settings (env-overridable, with on-disk defaults)
├── db.py            # read-only sqlite connection helpers (+ ATTACH for cross-db joins)
├── queries.py       # ALL SQL lives here, returned as plain dicts/lists
├── messages_api.py  # HTTP client for the messages service's admin API
├── templates/       # base.html (shell + nav), index.html, messages.html
└── static/          # dashboard.js, messages.js, style.css
```

Two pages. `base.html` carries the nav and a `{% block scripts %}` that each
page overrides with its own script — `dashboard.js` reaches for elements that
only exist on the clients page, so it must not load on the others.

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

## Active-clients chart: rolling snapshots

`last_seen` only stores each client's *most recent* day, so a live "active per day" count is wrong
for past days — and visibly decays through the day as clients return. Fix: an hourly snapshot
freezes each day's active set as it happens.

- **Store** (`config.DAILY_ACTIVE_DB_PATH`, default `data/daily_active.db`, dashboard-owned,
  writable, git-ignored): `daily_active(date, client_id, is_cloud)` + `snapshot_runs(date,
  computed_at, client_count)`. Per-client rows (not a bare count) so week/month stay correct
  (distinct users) and the cloud toggle works on history.
- **Snapshot script** `scripts/snapshot_active.py` records, for a UTC day, every client whose
  `last_seen` is that day (with `is_cloud` via reverse DNS). Rows accumulate, so a run only ever
  *adds* clients to a day; `--date` for manual/backfill, `--replace` to rebuild a day from scratch
  (destructive — it discards clients whose `last_seen` has since moved on). Run hourly by cron
  (`5 * * * *`) over **yesterday and today**: re-doing yesterday is what captures clients active in
  the last minutes before midnight, which a single end-of-day run structurally missed. Cron pings
  healthchecks.io on success — this job once died silently for 8 days.
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

## Filtering by client OS

The **OS** drop-down (`#client-os`, a checkbox panel behind a toggle button — not a native
`<select>`) restricts the badges, both charts, and the clients table to one or more platforms; no
checkbox checked means all platforms. Options are served by `/api/client-os`
(`queries.client_os_values()` — `SELECT DISTINCT client_os`) rather than hard-coded, so a newly
reported platform appears on its own; `OS_LABELS` in `dashboard.js` only spells the known values
("ios" → "iOS") and falls back to the raw value. Like the cloud toggle, changing it refreshes
immediately. The frontend sends one `client_os` query param per checked value
(`URLSearchParams.append`); `main._client_os()` reads them with `request.args.getlist`, and
`queries._os_predicate` turns the list into an `IN (?, ...)` clause — always bound params, never
inlined.

One wrinkle: `hist.daily_active` stores no OS, so filtering the *active* chart and the snapshot-backed
badges (`all_week` / `all_yesterday`) joins history back to `clients` for the client's **current** OS
(`_hist_os_join`). Clients no longer in `clients` therefore drop out of OS-filtered results — so the
per-OS counts can sum to slightly less than the unfiltered total on those two paths. That is correct
(their OS is unknowable), not a bug. `first_seen`-based and `hour`-granularity paths are unaffected.

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

## The Messages page

`/messages` composes the broadcast messages the app and the web app show. It is
the one page that is not backed by a SQLite read:

- **It talks HTTP, not SQL.** Messages live in the messages service's own
  database, which that service writes. The page calls `/admin-api/*` on this
  app, which forwards to `http://thewave-messages:5005/admin/*` with the
  `x-admin-key` from `MESSAGES_ADMIN_KEY`. The key never reaches the browser,
  and the dashboard's read-only-connections rule survives intact. Do not be
  tempted to open `messages.db` directly — it is a *writable* database, and the
  invariant is documented in three places for a reason.
- **`/admin-api/*`, not `/api/*`, because it writes.** `CORS(app)` is scoped to
  `/api/*` so a cross-origin page cannot preflight its way into the write
  endpoints just because the operator is on the Tailnet.
- **The server owns both hard bits.** Markdown preview renders the parse tree
  returned by `POST /admin/preview` rather than parsing in JS, and
  `messages_api.py` converts London wall-clock to UTC on submit and back for
  display. Adding a second markdown parser or doing timezone maths in
  `messages.js` reintroduces exactly what those two choices avoid.
- Errors from the service are relayed verbatim, status and all — its validation
  strings are written for the person composing the message.
- **Help text lives in one place.** The `?` beside a compose field is a
  `<button class="help" data-help="…">`; the wording is the `HELP` map in
  `messages.js`, and `wireHelp()` opens one shared `.help-box` for all of them.
  Add a field, add its entry there — not a `title=` attribute or a second box.
  These strings describe rules the *messages service* owns (`targeting.py`,
  `models.py`, `docs/messages-client-guide.md`); change them together.
- **Banner-only fields hide with the type.** `dismissable_at` /
  `dismissable_after` hold a banner on screen, and the service *rejects* them on
  a modal or an inbox message rather than ignoring them. `#dismissal-row` shows
  and hides on the Display select, and `dismissalPayload()` sends explicit
  nulls off a banner — otherwise a delay left in the form after switching type
  turns a save into a 400 with no visible cause. Any future field with that
  shape wants the same pair of moves.

## Conventions to keep

- All SQL goes in `queries.py`; validate any user-supplied sort/column against an allow-list before
  interpolating it into SQL (everything else uses bound `?` params).
- Endpoints return plain JSON; the frontend owns all formatting.
- This is its own git repo — keep everything self-contained here.
