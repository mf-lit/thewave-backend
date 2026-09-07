# Broadcast messages — client guide

For the agent adding this to the Waveform app. Self-contained — you do not need
the server source.

**This is additive and non-breaking.** The server is already deployed and
nothing reads it until the app does, so there is no lockstep release. Ship when
it is ready.

**All three platforms get messages, web included.** That is the reason this is
a new service rather than a feature of the notifications one, whose screen is
unreachable on web: the notifications API is not proxied for the web build and
`NotificationProvider` skips its fetch entirely. Messages must not have that
shape.

## What it is

A message is something the operator wants to tell users: a lagoon closure, a
release note, a welcome for new installs. The server decides **who** sees each
one; the app decides **how** to show it and tells the server when it has been
seen.

## Endpoints

Two, both needing an `x-api-key` header. Base URL follows the same rule as
`lib/utils/api_config.dart` already does for the calendar:

```dart
static String get messagesBaseUrl =>
    kIsWeb ? '/api/messages' : 'https://wave-messages.vq5.net/messages';
```

On web, nginx proxies `/api/messages` and `/api/messages/acks` and injects the
key server-side, so **the web build must not carry a key at all** — same
arrangement as `/api/calendar`. On mobile, send the key.

### `GET /messages`

Headers: `x-api-key`, `X-Client-ID` (**required**), `X-Client-OS`,
`X-Client-Version`.

```json
{
  "messages": [
    {
      "message_id": "ab62dbeb-…",
      "revision": 1,
      "title": "Lagoon closed Tuesday",
      "body": "Closed for **maintenance**.\n\n- morning\n- afternoon",
      "display": "modal",
      "level": "info",
      "retain": false,
      "expires_at": null,
      "action_url": "https://thewave.com",
      "action_label": "Book now",
      "dismissable_at": null,
      "dismissable_after": null
    }
  ],
  "ttl": 900
}
```

Already ordered — highest `priority` first, then newest. **Render in the order
given**; do not re-sort.

### `POST /messages/acks`

```json
{"client_id": "…", "acks": [{"message_id": "…", "revision": 1}]}
```

→ `200 {"recorded": 2}`

Idempotent. Batch every message you have shown into one call.

**Send `X-Client-ID` on this request too**, even though `client_id` is in the
body. On web the Cloudflare rule and nginx both require the header on every
`/api/*` request and will 403 without it, before the body is ever read. This is
the single most likely deployment-only bug in this change — it will work in
local development and fail on `waveform.vq5.net`.

## Acks are what stop a message coming back

The server serves a message until the client acks it. There is no other
mechanism — no read flag, no per-user list.

- Ack a message once you have **shown** it, not when the user dismisses it. A
  modal the user swipes away has still been shown.
- An ack names a `revision`. It suppresses that revision and every earlier one.
  If the operator edits a message and bumps its revision, it comes back — that
  is deliberate, and it is how a corrected closure notice reaches people who
  saw the wrong one.
- **Keep a local seen-set as a safety net.** A dropped ack — offline, app
  killed mid-flush — otherwise re-shows a modal on the next poll. Follow
  `tutorial_service.dart`'s `whats_new_shown_<id>` pattern, keyed on
  `<message_id>:<revision>` so a genuine revision bump still gets through.
- Acking an unknown `message_id` is a `200`, not an error, so a client holding
  a since-deleted message can still flush its queue. Never let a failed ack
  block the others.

## `retain` means one thing only

**`retain` says whether the message stays in the inbox after it has been read.**
It has nothing to do with whether the server serves it again — the ack is what
stops that.

- `retain: false` — show it, ack it, drop it. Nothing persists.
- `retain: true` — show it, ack it, and **keep it in a local inbox** so the user
  can find it again. Persist it yourself; the server will not send it a second
  time. Follow the single-JSON-blob pattern in
  `wetsuit_storage_service.dart` (`prefs.setString` of a `jsonEncode`d list).
- `expires_at` (nullable, UTC ISO-8601) is when a retained message should leave
  that local inbox. `null` means never. Only meaningful when `retain` is true.

## Display rules

| `display` | Behaviour |
|---|---|
| `modal` | A dialog, at most **one per foreground**. Show the first in the list; send the rest to the inbox. |
| `banner` | Inline on the schedule screen. |
| `inbox` | List only — never interrupts. |

`level` is `info` or `warning` and is styling only: a warning is more prominent,
not more blocking.

### Banners that cannot be dismissed yet

> Also covered on its own in `dismissal-delay-client-change.md`, for an agent
> already implementing from this guide when the feature landed. The two say the
> same thing — change them together.

Two optional fields hold a banner on screen so it is actually read. They are
**always present in the payload**, `null` on anything that is not a banner — so
read one shape rather than branching on `display` to know which keys exist.

| Field | Type | Meaning |
|---|---|---|
| `dismissable_at` | UTC ISO-8601, nullable | Before this moment, the user cannot dismiss the banner. |
| `dismissable_after` | duration string, nullable | Same lock, counted from **when you first show it** — `"30s"`, `"5m"`, `"2h"`. |

Both `null` — which is the common case — means dismissable immediately, exactly
as banners behave today.

**When both are set, the earlier one unlocks it.** Compute the unlock moment as:

```
min(
  dismissable_at              (if set),
  first_shown_at + dismissable_after   (if set)
)
```

and hide the dismiss control until then. It is a `min`, not an `and` — whichever
comes first wins. Because the `dismissable_after` countdown starts the moment you
draw the banner, a short one will usually beat a later `dismissable_at`; that is
intended, not a bug to work around.

Three things to get right:

- **Persist `first_shown_at` per message**, alongside your seen-set. Holding it
  in memory means backgrounding and reopening the app restarts the countdown,
  and the user faces the same lock again.
- **Parse `dismissable_after` defensively.** It is `<integer><unit>` with `s`,
  `m` or `h`, and the server guarantees that shape — but treat anything you
  cannot parse as "no delay" rather than as "locked forever". Failing open is
  the right direction: the worst case is a banner the user can close.
- **The lock is client-side only.** The server does not enforce it and will keep
  serving the message until you ack it. Ack on show as usual — acking does not
  dismiss, and the two are unrelated.

`action_url` and `action_label` are **both-or-neither**. When present, render a
button labelled `action_label` opening `action_url` (`url_launcher` is already
a dependency). The URL is always `https://`.

## Body format

A closed markdown subset. **The server validates every body on write and
rejects anything outside this grammar**, so your parser can be strict and small
— it never has to guess or fall back to rendering raw text.

- **Blocks** split on one or more blank lines. A block whose **every** line
  starts with `- ` is a bullet list; otherwise it is a paragraph, and its lines
  join with a **single space**.
- **Inline**, in paragraphs and list items alike: `**bold**`, `*italic*`,
  `[label](https://…)`. **No nesting** — a link label and the inside of an
  emphasis run are plain text.
- **Escapes**: `\\`, `\*`, `\[`, and nothing else.
- URLs always start with `https://`.
- Limits: `title` ≤ 100 characters, `body` ≤ 2000, `action_label` ≤ 30.

**Do not add `flutter_markdown`.** The grammar is six constructs; a hand-written
`TextSpan` builder is smaller than the dependency and cannot render something
the server did not sanction.

Two consequences worth knowing, because they look like bugs and are not:

- A bare `*` or `[` cannot reach you. The server rejects it, so `2 * 3` arrives
  as `2 \* 3`. Your parser may treat an unescaped delimiter as unreachable.
- A list always has a blank line before it. `Closures:\n- one\n- two` is a
  single **paragraph** reading "Closures: - one - two", because its lines do not
  all start with `- `. The dashboard previews this, so it should not ship, but
  render it faithfully if it does.

## Polling

- On startup, and on `AppLifecycleState.resumed`.
- Throttle with the `ttl` in the response (seconds; currently 900). Do not poll
  more often than that, and do not hard-code the number — it is served so it can
  change without an app release.
- The response is `Cache-Control: no-store` and must never be cached. On web it
  is per-client and the edge is explicitly configured not to store it; do not
  add a caching layer of your own.
- A failed poll is a no-op. Never block the schedule screen on it.

## Where this lands in the app

Suggested, so you do not have to hunt:

- A `MessagesProvider` in the `MultiProvider` list in `lib/main.dart:254`.
- A service alongside `lib/services/calendar_service.dart`.
- The modal: copy `lib/widgets/get_the_app_dialog.dart` and wrap it in
  `DialogLayout.constrain` (`lib/utils/dialog_layout.dart`, 560px cap) — an
  unconstrained dialog spans a maximised browser window.
- The inbox screen: model on `lib/screens/notifications_screen.dart`, reached
  from the overflow `PopupMenuButton` in `lib/screens/lake_schedule_screen.dart:686`.
- Seen-set persistence: `tutorial_service.dart`. Retained-message persistence:
  `wetsuit_storage_service.dart`.

## Errors — body is always `{"error": "<message>"}`

| Status | Cause |
|---|---|
| `400` | `Missing X-Client-ID header`, `Invalid X-Client-ID format`, or a malformed acks body |
| `401` | Missing or wrong `x-api-key` |
| `403` | **Web only.** nginx rejected the request — no `X-Client-ID`, or not from our own page |
| `429` | **Web only.** Rate limited at nginx or the edge. Back off; do not retry in a tight loop |

A `400` here is a client bug a correct implementation cannot produce — treat
them as assertions, not as messages to surface to the user.

## Client checklist

1. `messagesBaseUrl` in `ApiConfig`, `kIsWeb`-branched, **no key on web**.
2. A service with `fetch()` and `ack()`, sending `X-Client-ID`, `X-Client-OS`
   and `X-Client-Version` on both calls.
3. A `TextSpan` body parser for the six constructs. No new dependency.
4. Ack on **show**, batched, plus a local seen-set keyed
   `<message_id>:<revision>`.
5. A local inbox for `retain: true`, pruned on `expires_at`.
6. One modal per foreground, in the order served; the rest to the inbox.
7. For a banner with either dismissal field set, hide the dismiss control until
   `min(dismissable_at, first_shown_at + dismissable_after)`, persisting
   `first_shown_at` so backgrounding does not restart the countdown.
8. Poll on startup and resume, throttled by `ttl`.
9. Verify on web, not just on device — the `X-Client-ID`-on-acks requirement
   only bites there.
