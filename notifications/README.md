# Notifications

Watches session availability at The Wave and pushes to the waveform mobile app
when something a user is waiting for changes.

Two processes over one SQLite file:

- **API** (`src.wsgi:app`, port 5001) — clients register a device token and the
  sessions they care about.
- **Worker** (`python -m src.worker`) — polls the upstream calendar API for the
  sessions that are due a check, and pushes via Firebase Cloud Messaging.

## Layout

```
src/
  settings.py         env vars + config.yaml (mtime-cached, so keys rotate live)
  clock.py            Europe/London session times vs UTC storage timestamps
  db.py               per-thread connections, WAL, busy timeout
  migrations.py       additive-only, tracked by PRAGMA user_version
  models.py           validation; the client-visible error strings
  repository.py       all SQL
  calendar_client.py  upstream calendar API
  scheduling.py       how long until the next check
  evaluator.py        pure notify/don't-notify decision
  push.py             FCM payloads and delivery
  worker.py           the check loop
  admin.py            maintenance CLI
  api/                Flask factory, auth, routes, error handling
tests/
```

Two other services read the database directly — the dashboard ATTACHes it
read-only, and sqlite-web browses it — so migrations only ever add.

## Running

```bash
uv sync
uv run pytest

# API
uv run gunicorn --workers 2 --bind 0.0.0.0:5001 src.wsgi:app
# Worker
uv run python -m src.worker
```

In production both run as containers from the same image; see
`/thewave/docker-compose.yml`.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SQLITE_DB_PATH` | `data/notifications.db` | database file |
| `NOTIFICATIONS_CONFIG_PATH` | `config/config.yaml` | API keys, calendar key |
| `CALENDAR_API_URL` | `http://localhost:5000/calendar` | upstream calendar |
| `CALENDAR_API_KEY` | — | overrides `calendar_api_key` in the config file |
| `GOOGLE_APPLICATION_CREDENTIALS` | `config/google_application_credentials.json` | Firebase service account |
| `DISABLE_API_AUTH` | unset | skip API key checks (local development only) |
| `WORKER_HEARTBEAT_PATH` | `data/worker-heartbeat` | touched each cycle; the container healthcheck reads it |
| `LOG_LEVEL` | `INFO` | |

`config/config.yaml` (see `config.yaml.example`):

```yaml
api_keys:
  - <key>
calendar_api_key: <key for the upstream API>
```

The API refuses to start if this file has no keys and `DISABLE_API_AUTH` is
unset. Edits are picked up without a restart.

## API

Every endpoint except `/health` requires an `x-api-key` header, and every path
is scoped to a client UUID. Errors are always `{"error": "<message>"}`.

### `POST /clients/{client_id}/notifications` → 201

```json
{
  "performance_ak": "TWB.EVN6.PRF8962",
  "date": "2026-08-05",
  "time": "18:00",
  "side": "right",
  "notification_type": "below_threshold",
  "thresholds": [5, 2]
}
```

`side` is `left`, `right` or `none`. `time` accepts `HH:MM`, `HH:MM:SS` or
`HH:MM:SS.mmm` and is stored as `HH:MM`. `thresholds` is required for
`below_threshold` and ignored otherwise.

- `below_threshold` — push the first time availability falls to or below each
  threshold. Each threshold fires once.
- `above_zero` — push when a sold-out session gets a seat back. Re-arms if it
  sells out again.
- `quiet_session` — push once, shortly before a session starts, if it still has
  plenty of room. Takes `minimum_slots` and `time_before` instead of
  `thresholds`:

```json
{
  "performance_ak": "TWB.EVN6.PRF8962",
  "date": "2026-08-05",
  "time": "18:00",
  "side": "right",
  "notification_type": "quiet_session",
  "minimum_slots": 12,
  "time_before": "24h"
}
```

`minimum_slots` is a separate field rather than a reused `thresholds` because
the comparison runs the other way: a threshold fires at or *below* its value,
`minimum_slots` at or *above*. Sharing one field would leave the direction
implied by `notification_type` and invisible to anything reading the value.

`time_before` is a duration string from `1h` to `48h`, carrying its unit so
other units can be added later. Unlike the polled types this one is checked a
single time, at roughly `session_start - time_before` — near enough on the
worker's 30–60s cycle — and then goes quiet until the session starts and it is
deleted. Created closer to the session than `time_before`, it checks at once.

The performance is validated against the calendar API, so a bad
`performance_ak` gives 404 and a side that isn't sold gives 400.

- `any_quiet_session` — the same idea, but for *any* session of a given kind
  rather than one you have already picked out. It names a title and a side
  instead of a performance, and pushes for every matching session that is quiet
  within the next `time_before` hours:

```json
{
  "notification_type": "any_quiet_session",
  "title": "Advanced Surf",
  "side": "right",
  "minimum_slots": 8,
  "time_before": "24h"
}
```

`performance_ak`, `date` and `time` are neither required nor accepted here —
there is no one session to name — and `title`, which every other type reads off
the calendar, is required instead. Each matching session is pushed for at most
once, ever; a session that goes quiet, fills up and goes quiet again does not
push twice.

Titles are matched case-insensitively but otherwise literally, so
`"Advanced Surf"` matches neither `"Advanced Surf Lesson"` nor
`"Advanced Coaching (In Water)"` — the parenthesised parts upstream uses are
what tell two session types apart. Send the title exactly as the calendar
spells it.

Nothing is validated against the calendar: no lookup, no 404, no 400, and
creation works during an upstream outage. Titles come and go seasonally and the
row outlives any one of them, so watching for a title that is not currently
scheduled is legitimate — the cost is that a typo silently never fires.

This is the one type with no session to expire against, so it is **never
deleted automatically**. It scans until the client deletes it.

Three optional filters narrow which sessions it will push for. They apply to
this type alone — every other type already names one session, so a filter on it
would either restate that session's own start or silently stop a watch the user
deliberately created, and both are refused with a 400:

```json
{
  "notification_type": "any_quiet_session",
  "title": "Advanced Surf",
  "side": "right",
  "minimum_slots": 8,
  "time_before": "48h",
  "days": ["sat", "sun"],
  "not_before": "09:00",
  "not_after": "13:00"
}
```

`days` is `"mon"`…`"sun"`, case-insensitive and stored deduplicated in calendar
order; `not_before` and `not_after` are `HH:MM` and **inclusive** at both ends.
Each is independent and each is optional, so a row carrying none of them — which
is every row written before they existed — matches exactly what it always did.
An empty `days` list is rejected rather than read as "every day", since omitting
the field already spells that and a list that arrived empty is a client that
built it from an empty selection.

They narrow, never widen: a session must be inside the `time_before` window
*and* on an allowed day *and* inside the time range. Weekdays come off the
Europe/London calendar date, so a 00:30 BST Sunday session is a Sunday and not
the Saturday it is in UTC.

Responses carry `thresholds` only for `below_threshold`, `minimum_slots` and
`time_before` only for the two quiet types, `days`/`not_before`/`not_after` only
for an `any_quiet_session` that set them, and `last_checked_availability`
only once the worker has read it. `performance_ak`, `date` and `time` are
always present, empty for an `any_quiet_session`:

```json
{
  "notification_id": "...", "client_id": "...", "performance_ak": "...",
  "date": "2026-08-05", "time": "18:00", "side": "right",
  "title": "Advanced Surf", "notification_type": "below_threshold",
  "thresholds": [5, 2], "created_at": "2026-07-30T18:45:07.127058"
}
```

### `GET /clients/{client_id}/notifications` → 200

A bare JSON array.

### `DELETE /clients/{client_id}/notifications/{notification_id}` → 200

`{"message": "Notification deleted"}`, or 404 if it isn't that client's.

Notifications are also deleted automatically once their session starts.

### `PUT /clients/{client_id}/fcm-token` → 200

`{"fcm_token": "..."}` → `{"message": "FCM token saved successfully", "updated_at": "..."}`.
Tokens must be 140–200 characters of `[A-Za-z0-9:_-]`. Sending a new one
replaces the old.

### `GET /clients/{client_id}/fcm-token` → 200 / 404

`{"has_token": true}`. The token itself is never returned.

### `DELETE /clients/{client_id}/fcm-token` → 200 / 404

### `GET /health` → 200

`{"status": "ok"}`, no auth.

## Push payload

The Flutter app parses the FCM `data` map and iOS renders the notification
block, so both are a contract with the shipped app — changing a key or a
string needs an app release. `tests/test_push.py` pins them.

```
title  "Advanced Surf: 5th Jan at 18:00"
body   "Availability dropped to 3 on the right"                   (below_threshold)
       "A session has become available"                           (above_zero)
       "Possible quiet session: 12 slots remaining on the right"  (both quiet types)
data   performance_ak, date, time, side, session_title, availability,
       notification_type, notification_id, threshold, minimum_slots
```

`on the <side>` is omitted entirely when `side` is `none` — "Possible quiet
session: 12 slots remaining" — since a whole-lagoon session has no side to name
and the clause used to render as "on the none". The `side` key in the `data`
map is unaffected and still carries `none` verbatim.

`threshold` is the count at or below which `below_threshold` fires;
`minimum_slots` is the count at or above which the quiet types do. Opposite
senses, so they are separate keys — each is `""` for the types it does not
apply to.

An `any_quiet_session` push describes the session that matched, not the row:
`performance_ak`, `date`, `time` and `session_title` are the matched session's,
so the app can deep-link it. They will not correspond to any notification the
app holds locally, since the row itself stores none of them.

A token FCM reports as unregistered, or as belonging to another Firebase
project, is deleted.

## Check intervals

Two tables in `scheduling.py`, one keyed on days until the session and one on
seats remaining; the tighter wins. Sold-out and imminent is polled every three
minutes, distant and empty every four hours. Each notification carries its own
`next_check_at`, so the worker only ever fetches the dates it needs.

`quiet_session` opts out of both tables. Its `next_check_at` is written once at
creation, and once checked it is parked just past the session start — so it can
never come due again before the worker deletes it. If the calendar has no usable
seat count at that moment it retries for 30 minutes and is then abandoned
unfired, since a "starting soon, and quiet" push hours late would describe a
seat count that no longer means what the user asked about.

`any_quiet_session` opts out too, on the opposite grounds: it watches a window
rather than one session, so neither "days until" nor "seats left" is defined for
it. It rescans every `ROLLING_SCAN_MINUTES` (5), scheduled onto a fixed grid
rather than `now + 5min` so that every rolling row comes due in the same cycle
and shares one calendar fetch — the upstream cost stays flat as rows are added.
A window is at most three calendar days, and the upstream proxy caches days for
`CACHE_TTL_SECONDS`, so most scans are served without touching The Wave.

## Maintenance

```bash
docker exec thewave-notifications-worker uv run python -m src.admin list
docker exec thewave-notifications-worker uv run python -m src.admin delete-client <uuid> [--token]
docker exec thewave-notifications-worker uv run python -m src.admin clear-thresholds
docker exec thewave-notifications-worker uv run python -m src.admin clear-notified
docker exec thewave-notifications-worker uv run python -m src.admin prune-tokenless [--dry-run]
```

`list` has a `WHEN` column carrying any day and time-of-day filter — the first
place to look when a rolling watch is not firing, since a filtered one skips
sessions silently. `BAD DAYS` there means the `days` column could not be read
and the worker is refusing to scan that row at all, which from the outside is
indistinguishable from nothing having been quiet.

`clear-thresholds` re-arms every `below_threshold` notification and
`clear-notified` every `any_quiet_session` (both useful for testing delivery).
`prune-tokenless` drops notifications whose client has no token, and client rows
whose token is blank.

`prune-tokenless` matters more than it used to: `any_quiet_session` is the first
type that never deletes itself, so an uninstalled app leaves a row scanning
every five minutes indefinitely.

## Schema

```sql
CREATE TABLE notifications (
    client_id TEXT NOT NULL,
    notification_id TEXT NOT NULL,
    performance_ak TEXT NOT NULL,     -- '' for any_quiet_session
    date TEXT NOT NULL,               -- session date, Europe/London; '' as above
    time TEXT NOT NULL,               -- HH:MM, Europe/London; '' as above
    side TEXT NOT NULL,
    title TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    thresholds TEXT,                  -- JSON array, below_threshold only
    notified_thresholds TEXT,         -- JSON array of thresholds already sent
    last_checked_availability INTEGER,
    created_at TEXT NOT NULL,         -- naive UTC ISO-8601
    next_check_at TEXT,               -- 'YYYY-MM-DD HH:MM:SS' UTC
    minimum_slots INTEGER,            -- quiet types only, fires at or above
    time_before TEXT,                 -- e.g. '24h', quiet types only
    notified_performances TEXT,       -- JSON object of performance_ak -> session
                                      -- date, any_quiet_session only
    days TEXT,                        -- JSON array of 'mon'..'sun', calendar
                                      -- order; any_quiet_session only, NULL
                                      -- means no day filter
    not_before TEXT,                  -- HH:MM Europe/London, inclusive; as above
    not_after TEXT,                   -- HH:MM Europe/London, inclusive; as above
    PRIMARY KEY (client_id, notification_id)
);

CREATE TABLE clients (
    client_id TEXT PRIMARY KEY,
    fcm_token TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    alias TEXT                        -- set out of band; the dashboard's label
);
```

Migrations live in `migrations.py` as code, tracked by `PRAGMA user_version`,
and are additive only — the dashboard attaches this database read-only. That is
why `any_quiet_session` stores `''` in the three `NOT NULL` columns that
identify a session rather than relaxing them: dropping a constraint means
rebuilding the table. `clock` reads `''` as unparseable, so nothing mistakes
such a row for a dated one.
