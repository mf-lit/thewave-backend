# API change: day and time-of-day filters on `any_quiet_session`

For the agent extending the mobile app. Self-contained — you do not need the
server source. Companion to `quiet-session-client-guide.md`, which still
describes everything else correctly.

**This is additive and non-breaking.** Existing app builds keep working
unchanged, and watches already created keep behaving exactly as they do today.
Nothing needs to ship in lockstep.

## What changed

`any_quiet_session` — the standing "tell me when any Advanced Surf frees up"
watch — accepts three new **optional** fields that narrow which sessions it
pushes for:

| Field | Type | Format |
|---|---|---|
| `days` | array of string | `"mon" "tue" "wed" "thu" "fri" "sat" "sun"`, non-empty |
| `not_before` | string | `HH:MM` (also accepts `HH:MM:SS`, `HH:MM:SS.mmm`) |
| `not_after` | string | as above |

```json
POST /clients/{client_id}/notifications
x-api-key: <key>

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

→ pushes only for a session starting **on a Sat or Sun**, **between 09:00 and
13:00 inclusive**, in the next 48h, with 8+ free on the right.

## Semantics you must mirror in the UI

- **All three are independent and optional.** Send any subset. Sending none is
  the current behaviour.
- **They only ever narrow.** A session must satisfy `time_before` **and** `days`
  **and** the time range. There is no "OR" — one watch cannot express "weekday
  evenings or weekend mornings". That needs two watches.
- **Both time bounds are inclusive.** `not_before: "09:00"` *includes* a 09:00
  session. Word the UI as "from 09:00" / "until 13:00", not "after/before".
- **Times are the session's start time**, in **Europe/London** (what the user
  reads on the booking page). Do not convert to device local time — send the
  wall-clock time the user picked. Do not send a timezone offset.
- **Days are the session's London calendar day.** Do not derive them from a UTC
  timestamp.
- `not_before` may be sent without `not_after` and vice versa.

## Response contract

The three fields are **absent-or-present, never null**. Omit-checking is
required; do not model them as nullable keys that always appear.

```json
{
  "notification_id": "…", "client_id": "…",
  "performance_ak": "", "date": "", "time": "",
  "side": "right", "title": "Advanced Surf",
  "notification_type": "any_quiet_session",
  "minimum_slots": 8, "time_before": "48h",
  "days": ["sat", "sun"], "not_before": "09:00", "not_after": "13:00",
  "created_at": "2026-08-07T18:45:07.127058"
}
```

Values come back **canonical, not as you sent them** — echo the response into
local state rather than assuming your request round-tripped verbatim:

- `days` is lowercased, **deduplicated, and reordered into calendar order**
  (`["SUN","sat","sun"]` → `["sat","sun"]`). Render from the response order;
  do not preserve the user's selection order.
- Times are normalised to `HH:MM` (`"9:00"` → `"09:00"`, `"13:00:00"` → `"13:00"`).

**There is no update endpoint.** Changing a filter on an existing watch means
`DELETE` then `POST` — a new `notification_id`. Plan the edit flow around that.

## Errors — `400`, body `{"error": "<message>"}`

| Cause | Exact message |
|---|---|
| Bad or empty `days` | `Invalid days. Expected a non-empty list of 'mon', 'tue', 'wed', 'thu', 'fri', 'sat' or 'sun'` |
| Bad `not_before` | `Invalid not_before format. Expected HH:MM, HH:MM:SS, or HH:MM:SS.mmm` |
| Bad `not_after` | `Invalid not_after format. Expected HH:MM, HH:MM:SS, or HH:MM:SS.mmm` |
| `not_before` later than `not_after` | `not_before must be earlier than not_after` |
| Any of the three on another type | `days is only valid for any_quiet_session notification_type` (likewise `not_before`, `not_after`) |

One error per response, first failure wins. These are all client bugs a correct
UI cannot produce — treat them as assertions, not as messages to surface.

## Rejections that will bite you

- **`"days": []` is a 400, not "every day."** A day picker with nothing selected
  must send **no `days` key at all**. This is the single most likely bug.
- **Full day names are rejected.** `"saturday"` → 400. Send `"sat"`.
- **Day numbers are rejected.** No `1`–`7`, no `0`–`6`. Strings only.
- **The fields are rejected on the other three types** (`below_threshold`,
  `above_zero`, `quiet_session`) rather than ignored. Only build the filter UI
  on the `any_quiet_session` path.
- An explicit `"days": null` is accepted and means "no filter", so a serialiser
  that emits nulls for absent fields is safe — but omitting is cleaner.
- `not_before: "22:00"`, `not_after: "02:00"` is a 400. Ranges do not wrap past
  midnight; nothing at The Wave runs across it.

## Unchanged

- Push payload — identical. Still describes the **matched session**, not the
  row, and still carries no filter fields.
- Every other field and rule for `any_quiet_session`: exact case-insensitive
  title matching, `minimum_slots`, `time_before` (`"1h"`–`"48h"`), the
  once-per-session-ever guarantee, `performance_ak`/`date`/`time` returned as
  `""`, and **the row is never deleted server-side** — you must `DELETE` it.
- The other three notification types, entirely.

## Client checklist

1. Add the three optional fields to the `any_quiet_session` request model and
   the notification response model, as **optional/absent-able**.
2. Filter UI on the create screen: a day multi-select and two optional time
   pickers, defaulting to no filter.
3. Serialise an empty day selection as an **omitted** key.
4. Store the **response's** canonical values, not the submitted ones.
5. Render the filter on the watch list — a user with several similar watches
   cannot otherwise tell them apart, since title and side may be identical.
6. Edit = `DELETE` + `POST`; expect a new `notification_id`.
7. Copy should say the times are the session's start, in UK time, inclusive.
