# Quiet-session notifications — client guide

For the app talking to the notifications API. Covers the two quiet types only;
`below_threshold` and `above_zero` are unchanged.

Both alert on a session having **plenty of room**. They are the mirror of
`below_threshold`, which alerts on a session running out — so `minimum_slots`
fires at or **above** its value, where `thresholds` fires at or **below**.

| | `quiet_session` | `any_quiet_session` |
|---|---|---|
| Watches | one session you already picked | any session matching a title + side |
| Identified by | `performance_ak` + `date` + `time` | `title` |
| Fires | once, ~`time_before` before that session | once per matching session, forever |
| Filterable by day/time | no | yes, optionally |
| Calendar-validated on create | yes (404 / 400) | no |
| Deleted automatically | yes, after the session starts | **never** |

Use `quiet_session` when the user is looking at a specific session. Use
`any_quiet_session` for a standing "tell me when an Advanced Surf frees up".

---

## Endpoints

All require `x-api-key: <key>`. `client_id` and `notification_id` must be UUIDs.

```
POST   /clients/{client_id}/notifications      → 201
GET    /clients/{client_id}/notifications      → 200  (array)
DELETE /clients/{client_id}/notifications/{id} → 200
PUT    /clients/{client_id}/fcm-token          → 200  {"fcm_token": "..."}
```

Register the FCM token before creating notifications — a push to a client with
no token is dropped silently, and the notification is not retried for it.

---

## `quiet_session`

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

All seven fields required. The server checks the performance exists and sells
that side, so expect `404` for a bad `performance_ak` and `400` for a side the
session doesn't sell. `title` is taken from the calendar — don't send it.

Checked exactly once, at roughly `session_start - time_before`. If the session
is quiet enough at that moment you get one push; otherwise you get nothing,
ever. Created closer to the session than `time_before`, it checks immediately.

The row is deleted once the session starts, whether or not it fired. Don't
treat a locally-cached list as authoritative — re-`GET` rather than assuming a
notification you created still exists.

---

## `any_quiet_session`

```json
{
  "notification_type": "any_quiet_session",
  "title": "Advanced Surf",
  "side": "right",
  "minimum_slots": 8,
  "time_before": "24h"
}
```

Those five fields are required, plus up to three optional filters below.
**Do not send** `performance_ak`, `date` or `time` — they are ignored, and the
server stores `""` for all three.

Every ~5 minutes the server scans all sessions starting within the next
`time_before` hours, and pushes for each one whose title and side match and
which has at least `minimum_slots` free. Each session is pushed for **at most
once, ever** — a session that goes quiet, fills up and goes quiet again does
not push twice.

**Titles must match exactly, case aside.** Nothing is normalised: no prefix
matching, no punctuation stripping. `"Advanced Surf"` matches neither
`"Advanced Surf Lesson"` nor `"Advanced Coaching (In Water)"`, and the
parenthesised parts are meaningful — `"Performance Coaching (ADV+)"` and
`"Performance Coaching (EXP T)"` are different sessions. Populate this from a
picker fed by the calendar feed you already render; a hand-typed title that
doesn't match anything is accepted and simply never fires.

Nothing is validated against the calendar, so creation succeeds during an
upstream outage and for a title with no sessions currently scheduled.

**This type is never deleted server-side.** If the user turns the alert off,
you must `DELETE` it, or it keeps scanning indefinitely.

### Narrowing it by day and time

A user who can only surf at weekends does not want a push about a quiet Tuesday
07:00 session — and since this type is never deleted, that noise does not stop
on its own. Three optional filters cut it down:

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

That watch pushes only for a session starting on a Saturday or Sunday, between
09:00 and 13:00 inclusive, in the next 48 hours, with 8+ free on the right.

- `days` — a **non-empty** list of `"mon"`, `"tue"`, `"wed"`, `"thu"`, `"fri"`,
  `"sat"`, `"sun"`. Case-insensitive in; echoed lowercase, deduplicated and in
  calendar order, so `["SUN","sat"]` comes back as `["sat","sun"]`. Full names
  like `"saturday"` are **rejected**, as is `[]` — send no `days` key at all for
  "any day", because an empty list is what a picker with nothing selected
  produces and a watch that can never fire is not what the user meant.
- `not_before` / `not_after` — `HH:MM` (the same formats `time` accepts),
  **inclusive at both ends**: `"not_before": "09:00"` includes a 09:00 session.
  Either may be sent without the other. `not_before` later than `not_after` is
  rejected rather than read as wrapping past midnight.

All three are optional and independent, and they only ever narrow: a session has
to be inside the `time_before` window **and** on an allowed day **and** inside
the time range. Sending none of them is the behaviour this type has always had,
so **existing watches are unaffected** and you can adopt the filters whenever
you like.

Days are the Europe/London calendar day of the session, which is what the user
sees on the booking page — not UTC, so a 00:30 session in British Summer Time
counts as the day it reads as, not the one before.

The three fields are **`any_quiet_session`-only** and rejected with a `400` on
the other types, rather than accepted and ignored. On a type that names one
session a day filter can only agree with that session's own start or silence the
alert entirely, and the second one would be silent both ways.

---

## Shared field rules

- `side` — `"left"`, `"right"` or `"none"`. Whole-lagoon sessions (Beginner
  Lesson, Play In The Bay, Pilates…) are `"none"`-only. Course products also
  appear as `"none"` alongside a left/right pair.
- `minimum_slots` — non-negative integer. `0` matches everything including
  sold-out sessions; send an `int`, not a string.
- `time_before` — `"1h"` … `"48h"`. Whole hours with the `h` suffix; `"24"` and
  `24` are both rejected. Case-insensitive on the way in, echoed lowercase.

---

## Response shape

```json
{
  "notification_id": "…", "client_id": "…",
  "performance_ak": "TWB.EVN6.PRF8962", "date": "2026-08-05", "time": "18:00",
  "side": "right", "title": "Advanced Surf",
  "notification_type": "quiet_session",
  "minimum_slots": 12, "time_before": "24h",
  "created_at": "2026-07-30T18:45:07.127058"
}
```

`performance_ak`, `date` and `time` are **always present but empty strings** for
an `any_quiet_session`. Don't feed them to a date parser without checking.

`minimum_slots` and `time_before` appear only for these two types;
`days`, `not_before` and `not_after` only for an `any_quiet_session` that set
them, so **treat all three as absent-or-present**, not as nullable keys;
`last_checked_availability` only after the server has read a seat count, and
never for `any_quiet_session`.

---

## Push payload

```
title  "Advanced Surf: 5th Jan at 18:00"
body   "Quiet session: 12 slots remaining on the right"     ← both types
data   performance_ak, date, time, side, session_title, availability,
       notification_type, notification_id, threshold, minimum_slots
```

All `data` values are strings. `threshold` is `""` for both quiet types;
`minimum_slots` carries the configured value.

**The one thing that will bite you:** for `any_quiet_session`, the push's
`performance_ak`, `date`, `time` and `session_title` describe the **matched
session**, not the notification row — which stores none of them. They will
match no locally-held notification. Use them to deep-link the session, and use
`notification_id` (not `performance_ak`) if you need to find the row that
caused the push.

A 48h window over a quiet stretch can match several sessions at once, so be
ready to receive **several pushes in the same second**, each for a different
session, all carrying the same `notification_id`.

---

## Errors

`400` unless noted, body `{"error": "<message>"}`.

| Cause | Message |
|---|---|
| Missing field | `Missing required field: <name>` |
| Unknown type | `Invalid notification_type. Must be 'below_threshold', 'above_zero', 'quiet_session', or 'any_quiet_session'` |
| Bad side | `Invalid side. Must be 'left', 'right', or 'none'` |
| Missing title (`any_quiet_session`) | `title is required and must be a string` |
| Missing `minimum_slots` | `minimum_slots is required for <type> notification_type` |
| Bad `minimum_slots` | `minimum_slots must be a non-negative integer` |
| Missing `time_before` | `time_before is required for <type> notification_type` |
| Bad `time_before` | `Invalid time_before format. Expected a whole number of hours, e.g. '24h'` |
| Out of range | `time_before must be between 1h and 48h` |
| Bad `days` (incl. `[]`) | `Invalid days. Expected a non-empty list of 'mon', 'tue', 'wed', 'thu', 'fri', 'sat' or 'sun'` |
| Bad `not_before` / `not_after` | `Invalid <field> format. Expected HH:MM, HH:MM:SS, or HH:MM:SS.mmm` |
| Inverted time range | `not_before must be earlier than not_after` |
| A filter on another type | `<field> is only valid for any_quiet_session notification_type` |
| Unknown performance (`quiet_session`) | `Performance not found for performanceAK=…` — **404** |
| Side not sold (`quiet_session`) | `Side '<side>' not found for performance …` |

Omitting `notification_type` entirely reports `Missing required field:
performance_ak`, because the required set is chosen by the type.
