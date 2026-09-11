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
`banner_title`, `body`, `display`, `level`, `retain`, `expires_at`,
`action_url`, `action_label`, `dismissable_at` and `dismissable_after` — the
message and how to show it, and nothing about who else got it. The banner-only
fields are sent on every message, null where they cannot apply, so the client
reads one shape rather than branching on `display`. The response is
`Cache-Control: no-store`.

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
- Limits: title ≤ 100 characters, `banner_title` ≤ 100, body ≤ 2000,
  `action_label` ≤ 30.

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
- the client has not acked it at the current `revision` — **unless it is
  retained**, which keeps being served; see below.

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

## The five time-valued fields

They fall into three groups with three different owners, and **no rule crosses
between them**:

| Group | Fields | Owner | Question it answers |
|---|---|---|---|
| Delivery | `starts_at`, `ends_at` | server | when is it sent? |
| Retention | `retain`, `expires_at` | client | how long is it kept? |
| Interaction | `dismissable_at`, `dismissable_after` | client, banner only | when can it be closed? |

The complete rule set is three sentences, and only the first compares two times:

- `starts_at` must be earlier than `ends_at` — a window has to be a window, and
  both halves are the same idea.
- `expires_at` requires `retain`.
- `dismissable_at` / `dismissable_after` require `display: banner`.

Everything else is allowed, including arrangements that look odd and are not:
an `expires_at` before `ends_at` is how a retained message is cleared from
inboxes that already hold it, and a `dismissable_at` after `ends_at` is a banner
nobody can close for its whole life.

Two cross-group comparisons used to live here and both were wrong.
`expires_at >= ends_at` had it backwards — expiring a message *is* an
`expires_at` pulled back inside the delivery window. `dismissable_at <=
expires_at` coupled banner interaction to inbox retention, the two fields with
least to do with each other; it fired even when `retain` was false and
`expires_at` therefore meant nothing, and it forbade a mild case while
permitting the extreme one.

## Retained messages keep arriving

A `retain` message stays in the payload after the client has acked it, for as
long as it is live. Everything else stops at its ack, as before.

This exists so `expires_at` can be changed. It is an instruction the client
acts on out of the copy it stored, so an edit to it reaches nobody unless the
message is still arriving — without this, a retained message's expiry is fixed
the moment a client files it, and "drop this from every inbox" is
unimplementable.

**To expire a retained message from inboxes that already hold it**, set
`expires_at` to now and leave everything else alone. Do *not* bump the revision
and do *not* pull `ends_at` back: the message has to stay live long enough for
clients to poll and pick the new value up, which is one `ttl` — 15 minutes.
`ends_at` is therefore the propagation window; if it has already passed, push
it forward first.

`expires_at` is deliberately unconstrained by `ends_at` in either direction. It
was once required to be at or after it, which forbade exactly this edit.

The client must not re-show a refreshed message: its seen-set keys on
`(message_id, revision)`, and a revision bump is still what makes something
appear again. It must also not treat absence from a payload as deletion — that
usually just means `ends_at` passed. See
`docs/retained-refresh-client-change.md`.

## Holding a banner on screen

A banner can be made undismissable for a while, so a closure notice is actually
read rather than swiped away. Two optional columns, **banner only** — the
service rejects them on a `modal` or an `inbox` message rather than ignoring
them, because a silently dropped delay is one the operator believes is in force:

| Column | Meaning |
|---|---|
| `dismissable_at` | An absolute UTC time. Before it, the banner cannot be dismissed. |
| `dismissable_after` | A duration from when the client **first shows** the banner — `30s`, `5m`, `2h`. |

Neither is constrained by any other time on the message — see "The five
time-valued fields" above. A `dismissable_at` past `ends_at` is a banner nobody
can close for its whole life, which is allowed.

**Set both and the earlier one wins.** Whichever unlocks first unlocks the
banner — it is a floor on how long the message is unavoidable, not a guarantee
of how long it is seen. In practice a short `dismissable_after` will override a
later `dismissable_at`, since the countdown starts as soon as the client draws
it. Setting both is only useful when the fixed time might come first.

`dismissable_after` has no upper bound — how long a banner stays unavoidable is
the operator's call, and the message's own window bounds it in practice, since
it stops being served once `ends_at` passes. `0s` is accepted and means no
delay, the same as leaving it blank.

Neither is enforced server-side; both are served to the client, which does the
holding — see `docs/dismissal-delay-client-change.md`. `dismissable_after` stores the unit (`"30s"`, not `30`) for the same
reason `notifications.time_before` does — the column has to mean something to
whoever reads it in sqlite-web, and another unit can be added later without
reinterpreting old rows.

## Every banner says its own thing

`banner_title` is what the banner shows in the one line it has. `title` and
`body` are unchanged and are what the user reads on tapping through, so this is
a shorter way of saying the same thing, not a second message — nothing is ever
reachable only through it.

| | Shown where |
|---|---|
| `banner_title` | The banner on the schedule screen — "Closed today" |
| `title` + `body` | The message the banner opens — "Lagoon closed for maintenance until 6pm", and the detail |

**Required on a banner, rejected on anything else.** Those two errors are the
whole rule:

- `banner_title is required for banner messages` — absent, null and blank are
  one case, however the caller spells it.
- `banner_title is only valid for banner messages` — a modal and an inbox entry
  have nowhere to draw it, so storing one would be a silent no-op the operator
  believes is on screen. The same reason the dismissal fields are banner-only.

Requiring it is the point of the field. Falling back to `title` when it is
missing would mean most banners quietly kept showing a title written for a
dialog, which is the thing this exists to stop; making it a decision the
operator has to take is what changes that. The column stays nullable because a
modal has to store *something*, and NULL is that something.

Migration 3 backfills existing banners with their own `title`, so the rule holds
of the rows already in the file and not only of the ones written next. That
copies exactly what those banners already displayed — it changes no behaviour,
it moves a fallback out of the client and into the data.

Bounded by the same 100 characters as `title` rather than a shorter limit of its
own: it stands in for the title, so it can never need more room than one, and a
second number would only be a guess at how much of a line the client has.
Brevity is the point of the field, but it is the operator's judgement and the
compose form's prompting, not a rule. See `docs/banner-title-client-change.md`.
