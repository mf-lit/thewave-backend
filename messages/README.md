# Messages

Serves admin-authored broadcast messages to the waveform clients — iOS,
Android and web alike. There was previously no way to tell users anything:
the only server→client messaging was the version gate rewriting session
titles, and FCM push is user-created, availability-triggered, and unreachable
on the web app.

One process over one SQLite file:

- **API** (`src.wsgi:app`, port 5005) — clients fetch the messages they match
  and post acks; an admin surface on the same process authors them.

There is no worker. Nothing runs on a timer: a message becomes live because
its `starts_at` has passed, which is decided when a client asks.

## Layout

```
src/
  settings.py    env vars + config.yaml (mtime-cached, so keys rotate live)
  clock.py       aware-UTC timestamps, and a tolerant parser for them
  db.py          per-thread connections, WAL, busy timeout
  migrations.py  additive-only, tracked by PRAGMA user_version
  validation.py  the one exception the HTTP layer turns into a 400
  markdown.py    the closed grammar a message body may use
  versions.py    client version parsing, copied from upstream-api's gate
  models.py      validation; the admin-visible error strings
  targeting.py   pure who-sees-what rules, no database
  repository.py  all SQL
  directory.py   read-only reader over upstream-api's clients table
  admin.py       maintenance CLI
  api/           Flask factory, auth, routes, error handling
tests/
```

Still to come, per the build plan: the dashboard page, the infra wiring, and
`docs/messages-client-guide.md`.

The service reads a second database — upstream-api's `water_temperature.db` —
read-only, for the `days_count` that "how new is this user" targeting needs.
It deliberately keeps no first-seen table of its own: such a table would be
empty on the day this ships, so every existing user would look brand-new and
get the welcome message.

## Running

```bash
uv sync
uv run pytest

uv run gunicorn --workers 2 --bind 0.0.0.0:5005 src.wsgi:app
```

In production this runs as a container from this image; see
`/thewave/docker-compose.yml`.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `SQLITE_DB_PATH` | `data/messages.db` | database file |
| `MESSAGES_CONFIG_PATH` | `config/config.yaml` | client and admin API keys |
| `UPSTREAM_DB_PATH` | `/data/upstream-api/water_temperature.db` | read-only, for `days_count` |
| `MESSAGES_TTL_SECONDS` | `900` | how long a client may hold its message list |
| `DISABLE_API_AUTH` | unset | skip client API key checks (local development only) |
| `LOG_LEVEL` | `INFO` | |

`config/config.yaml` (see `config.yaml.example`):

```yaml
api_keys:
  - <key>
admin_keys:
  - <key>
```

Two separate lists, checked by two separate headers. `x-api-key` ships inside
the app and reaches `/messages`; `x-admin-key` reaches `/admin/*` and never
leaves this box. The API refuses to start if `api_keys` is empty and
`DISABLE_API_AUTH` is unset. With `admin_keys` empty it starts but registers
no admin routes at all, so a misconfigured deploy serves no admin surface
rather than an open one. `DISABLE_API_AUTH` does not apply to the admin keys.

Edits to the file are picked up without a restart.

## API

Errors are always `{"error": "<message>"}`.

### Client endpoints

Every one needs `x-api-key`. Reachable from the internet through the tunnel,
behind an edge allow-list that admits only these paths.

`GET /messages` → `200 {"messages": [...], "ttl": 900}`

Reads `X-Client-ID` (required — 400 if absent or not a UUID), `X-Client-OS`
and `X-Client-Version`. Each item carries `message_id`, `revision`, `title`,
`body`, `display`, `level`, `retain`, `expires_at`, `action_url` and
`action_label` — the message and how to show it, and nothing about who else
got it. The response is `Cache-Control: no-store`.

`POST /messages/acks` → `200 {"recorded": n}`

Body `{"client_id": "…", "acks": [{"message_id": "…", "revision": 1}, …]}`.
An idempotent upsert. An ack for a message that no longer exists is skipped
rather than rejected, so a client holding a since-deleted message can still
flush its queue.

`GET /health` → `200 {"status": "ok"}`, unauthenticated.

### Admin endpoints

Every one needs `x-admin-key`. The edge rules do not admit `/admin/*`, so
these are served only over the Docker network.

| Endpoint | Purpose |
|---|---|
| `GET /admin/messages` | every message, with `ack_count` and `matched_client_count` |
| `POST /admin/messages` | create → 201 |
| `GET\|PUT\|DELETE /admin/messages/{id}` | read, rewrite, remove |
| `POST /admin/messages/{id}/enabled` | the kill switch, body `{"enabled": bool}` |
| `POST /admin/audience` | dry-run a draft's targeting rules → `{"count", "sample"}` |

`PUT` rewrites every authored column — it is a replace, not a merge — and
moves `revision` only when the body says `"bump_revision": true`. Fixing a
typo should not re-show a modal to everyone who dismissed it; rewriting what
the message says should. That is a judgement about the edit, so it is the
caller's to make.

`POST /admin/audience` takes only the six targeting fields, not a whole
message: an operator setting up who a message reaches has usually not written
the copy yet.

## Admin CLI

```bash
docker exec thewave-messages-api uv run python -m src.admin <command>
```

`list`, `show <id>`, `enable <id>`, `disable <id>`, `delete <id>`,
`audience <id>`. It talks to the repository directly rather than over HTTP,
because it is the recovery path for when the dashboard, the admin key, or the
admin routes are the thing that is broken.

## Message bodies

Bodies use a closed markdown subset, validated on write so the Flutter parser
can be strict and small:

- **Blocks** split on one or more blank lines. A block whose every line starts
  with `- ` is a bullet list; otherwise it is a paragraph and its lines join
  with a single space.
- **Inline**: `**bold**`, `*italic*`, `[label](https://…)`. No nesting.
- **Escapes**: `\\`, `\*`, `\[`, and nothing else. A bare `*` or `[` is a
  validation error, not literal text.
- URLs must start with `https://`.
- Limits: title ≤ 100 characters, body ≤ 2000, `action_label` ≤ 30.

The trap worth knowing: a list needs a blank line before it. `Closures:` on
the line above two bullets makes one block whose lines do not *all* start with
`- `, so the whole thing is a paragraph. The dashboard's compose form previews
this live.

## Targeting

A message is served to a client when **all** of these hold:

- it is enabled, and `starts_at` has passed and `ends_at` has not;
- `client_ids` is empty or contains the caller's `X-Client-ID`;
- `os` is empty or contains the caller's lowercased `X-Client-OS`;
- the caller's `X-Client-Version` is within `[min_version, max_version]`,
  inclusive;
- the caller's `days_count` is within `[min_days_count, max_days_count]`,
  inclusive;
- the client has not acked it at the current `revision`.

An empty list and a missing one mean the same thing — everyone — and are
stored the same way, as NULL.

Two edge cases decided deliberately:

- A **missing or unparseable client version** is excluded from any message
  that sets either version bound, and included when neither is set. It fails
  closed on the constraint and open without one.
- A client with **no row in the upstream `clients` table** is treated as
  `days_count = 1`, maximally new. A fresh install may call `/messages` before
  it has ever called `/calendar`.

Editing a message does not re-serve it. Bumping its `revision` does — an ack
only suppresses the revision it names.
