# API change: banners that cannot be dismissed yet

For the agent extending the mobile app. Self-contained — you do not need the
server source. Companion to `messages-client-guide.md`, which still describes
everything else correctly.

**This is additive and non-breaking.** Existing behaviour is what a message with
both fields null does, and that is almost all of them. Nothing needs to ship in
lockstep; a build that ignores these two fields behaves exactly as it does now.

## What changed

A `banner` can now be held on screen for a while before the user is allowed to
dismiss it, so a closure notice is read rather than swiped away on sight. Two
new **optional** fields on every message in `GET /messages`:

| Field | Type | Meaning |
|---|---|---|
| `dismissable_at` | UTC ISO-8601 string, or `null` | Before this moment, the banner cannot be dismissed |
| `dismissable_after` | duration string, or `null` | The same lock, counted from when **you** first show it |

`dismissable_after` is `<whole number><unit>` where the unit is `s`, `m` or `h`
— `"30s"`, `"5m"`, `"2h"`. There is no upper bound.

```json
{
  "message_id": "c5791ce4-…",
  "display": "banner",
  "title": "Lagoon closed Tuesday",
  "body": "Closed all day for **maintenance**.",
  "dismissable_at": "2026-10-01T08:05:00+00:00",
  "dismissable_after": "30s"
}
```

## The one rule to get right

**Unlock at the earlier of the two.**

```
unlock_at = min(
  dismissable_at,                       // if not null
  first_shown_at + dismissable_after    // if not null
)
```

Hide the dismiss control until `unlock_at`. Both null — the common case — means
dismissable immediately.

It is a `min`, not an `and`: whichever comes first wins. This is a floor on how
long the banner is unavoidable, not a guarantee of how long it is seen. Because
the `dismissable_after` countdown starts the moment you draw the banner, a short
one will usually beat a later `dismissable_at`. That is intended — do not "fix"
it by taking the later of the two.

## Semantics you must mirror in the UI

- **Banner only.** They are always present in the payload and always `null` on a
  `modal` or an `inbox` message, so read one shape rather than branching on
  `display` to know which keys exist. The server rejects an operator who tries
  to set them on the other two types, so you will never see a non-null pair
  outside a banner.
- **`first_shown_at` must be persisted**, per message, alongside your seen-set.
  Held only in memory, backgrounding and reopening the app restarts the
  countdown and the user faces the same lock again. This is the most likely bug
  in this change.
- **The lock is entirely yours.** The server does not enforce it and keeps
  serving the message until you ack it. Ack on **show**, exactly as before —
  acking is not dismissing, and the two are unrelated. A banner can be acked
  and still locked.
- **`"0s"` is valid** and means no delay, the same as `null`. Do not treat it as
  a special case; the arithmetic already gives the right answer.
- **A long delay is legitimate.** There is no cap, so `"999h"` on a banner with
  no `ends_at` is a banner that cannot be dismissed for the rest of its life.
  That is a choice the operator is allowed to make — do not impose a ceiling of
  your own. What ends it is the message's own window: once `ends_at` passes it
  stops being served at all.

## Parse defensively, and fail open

The server guarantees the shape, so a strict parser is safe. But when something
does not parse, **treat it as no delay** rather than as locked:

- an unreadable `dismissable_after` → no delay from that field
- an unreadable `dismissable_at` → no delay from that field

Failing open is the right direction here. The worst case is a banner the user
can close a little early; the worst case of failing closed is a banner nobody
can ever get rid of, on a build you cannot hotfix.

## Errors

**None you can cause.** These fields are read-only to the client — you never
send them. Every rule above (banner-only, the duration format, `dismissable_at`
not being later than `expires_at`) is enforced on the admin API when the
operator composes the message, so an invalid combination cannot reach you.

`GET /messages` and `POST /messages/acks` are otherwise unchanged, including
their error responses.

## Unchanged

- **Acks**, entirely: ack on show, batch them, keep the local seen-set keyed
  `<message_id>:<revision>`, and send `X-Client-ID` as a header on the acks POST
  (still the deployment-only trap on web).
- **`retain` and `expires_at`** — still only about whether a message stays in
  the local inbox after it is read. Unrelated to dismissal, despite
  `dismissable_at` being bounded by `expires_at` on the server.
- **Modals and inbox entries**, entirely. One modal per foreground, in the order
  served.
- Every other field, the body grammar, polling, and the `ttl`.

## Client checklist

1. Add both fields to the message model as **nullable**.
2. Record and persist `first_shown_at` the first time a banner is drawn.
3. Compute `unlock_at` as the `min` above; hide the dismiss control until then.
4. Re-evaluate on a timer while the banner is visible, so the control appears
   without needing a redraw the user has no reason to trigger.
5. Treat an unparseable value as no delay, never as locked.
6. Check the countdown survives backgrounding the app — that is the bug this
   change is most likely to ship with.
