# API change: every banner carries its own, shorter title

For the agent extending the mobile app. Self-contained — you do not need the
server source. Companion to `messages-client-guide.md`, which still describes
everything else correctly.

**This is additive and non-breaking.** A build that ignores the new field still
shows a correct, complete message — nothing is ever reachable only through it.
Nothing needs to ship in lockstep.

## What changed

One new field on every message in `GET /messages`:

| Field | Type | Meaning |
|---|---|---|
| `banner_title` | string on a banner, `null` otherwise | What the **banner line** says, instead of `title` |

`title` and `body` are unchanged and are what the user reads when they tap the
banner open. `banner_title` exists because a banner has one line and a title
written for a dialog often does not fit it.

```json
{
  "message_id": "c5791ce4-…",
  "display": "banner",
  "banner_title": "Closed today",
  "title": "Lagoon closed for maintenance until 6pm",
  "body": "The lagoon is closed all day for **maintenance**."
}
```

## The one rule to get right

**The banner draws `banner_title`. Everything else draws `title`.**

```
banner line    :  banner_title
tapped through :  title + body            // always, even when banner_title is set
```

Do **not** substitute in the other direction: a banner still shows the full
`title` when opened. The pair is a summary and its expansion, not two names for
the same string.

## When it is set

**Non-null on exactly the banners, null on everything else.** The server
requires it when an operator composes a banner and rejects it on a `modal` or an
`inbox` message, and the banners that predate the field were backfilled with
their own `title`. So:

- every `display: "banner"` message you receive has one;
- every other message has `null`;
- the key is always present, so you never branch on `display` to know which keys
  exist.

**Write the fallback anyway.** `banner_title ?? title` costs a line, `title` is
never null, and it is the difference between a bad banner and a crash if a row
ever reaches you without one. Treat an empty or whitespace-only value the same
way — the server normalises those to null, but failing soft is free here.

## Semantics you must mirror in the UI

- **Plain text, not markdown.** Same as `title`. Only `body` is parsed. Render
  it as a single line and let it truncate; do not wrap it to two.
- **At most 100 characters**, the same bound as `title`. The operator is
  prompted to keep it short but is not forced to, so do not lay the banner out
  assuming a handful of words.
- **It changes nothing about acking, retention or dismissal.** A banner is
  still acked on show, still held by `dismissable_at` / `dismissable_after` if
  they are set, and `retain` still decides whether it is filed. `banner_title`
  is presentation and nothing else.

## Errors

**None you can cause.** The field is read-only to the client — you never send
it. Both rules that exist (required on a banner, rejected elsewhere, and the
length bound) are enforced on the admin API when the operator composes the
message, so an invalid value cannot reach you.

`GET /messages` and `POST /messages/acks` are otherwise unchanged, including
their error responses.

## Unchanged

- **Acks**, entirely: ack on show, batch them, keep the local seen-set keyed
  `<message_id>:<revision>`, and send `X-Client-ID` as a header on the acks POST
  (still the deployment-only trap on web).
- **The dismissal fields**, the `retain` / `expires_at` pair, and the
  retained-refresh rule.
- **Modals and inbox entries**, entirely — they never carry a banner title.
- The body grammar, polling, and the `ttl`.

## Client checklist

1. Add `banner_title` to the message model as **nullable** — it is null on
   every non-banner.
2. Draw `banner_title ?? title` in the banner, on one line.
3. Keep `title` and `body` as the tapped-through message, unchanged.
4. Check a banner whose two titles differ shows the short one inline and the
   long one when opened — the substitution is the whole change.
