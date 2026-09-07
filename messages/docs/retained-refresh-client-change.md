# API change: retained messages keep arriving

For the agent extending the mobile app. Self-contained — you do not need the
server source. Companion to `messages-client-guide.md`, which still describes
everything else correctly.

**This one is not purely additive.** It bends the ack rule, and a client that
ignores it will re-show every retained message on every poll. If you have
already built the inbox, this is the page to read.

## What changed

A `retain: true` message now **stays in the `GET /messages` payload after you
have acked it**, for as long as it is live. Everything else still stops dead at
its ack.

**Why:** `expires_at` is an instruction you act on out of the copy you stored.
If the message stopped arriving at its ack, an operator could never change it —
a retained message's expiry would be fixed the moment you filed it, and "drop
this from every inbox" would be impossible. Continuing to deliver it is the
only channel.

## What you must do

- **Consult your seen-set before showing anything.** It is keyed
  `<message_id>:<revision>`, which you are already keeping. Same key, already
  shown → file it silently. This is the whole of the work, and skipping it is
  the bug this change introduces.
- **Overwrite your stored copy from every payload.** The newest values win.
  `expires_at` in particular moves — that is the point.
- **Prune on every poll, not only on receipt.** Drop anything whose stored
  `expires_at` is in the past. A message that arrives already expired should be
  pruned rather than filed.
- **Do not re-ack.** Harmless — the upsert is idempotent — but pointless.

## The trap: absence is not deletion

**A message missing from the payload does not mean "remove it from the inbox."**
It usually means the message passed its `ends_at` and stopped being delivered,
and the user should still be able to read it until it expires.

Reconcile *forwards* only: take what arrives, update or add. Never diff the
payload against your inbox and delete the difference — that empties the inbox
the moment a campaign ends.

The only things that remove a retained message are its own `expires_at` passing,
and the user clearing it if you offer that.

## What you can now stop worrying about

The server's time fields fall into three independent groups, and **no rule
connects them any more**:

| Group | Fields | Question |
|---|---|---|
| Delivery | `starts_at`, `ends_at` | when the server sends it |
| Retention | `retain`, `expires_at` | how long you keep it |
| Interaction | `dismissable_at`, `dismissable_after` | when a banner can be closed |

So:

- **`expires_at` may fall before `ends_at`.** That is not a contradiction — it
  is exactly how a message is cleared from inboxes that already hold it. Do not
  clamp or "correct" it.
- **`dismissable_at` may fall after `ends_at` or after `expires_at`.** Also
  fine. Compute the unlock moment from the two dismissal fields alone.
- **`expires_at` now only ever appears with `retain: true`** — the server
  rejects it otherwise — so you can read it in the retained path and nowhere
  else.
- A `null` `expires_at` still means never. Such a message stays in the inbox
  until the user clears it, by design.

## Unchanged

- **Non-retained messages** stop at their ack exactly as before: show once, ack,
  drop.
- **A revision bump is still the only thing that makes a message show again**,
  retained or not.
- Acking on **show**, batching acks, and sending `X-Client-ID` as a header on
  the acks POST (still the deployment-only trap on web).
- Modals, banners, the body grammar, polling, `ttl`, and the dismissal fields.

## Errors

**None you can cause.** Nothing about this changes what you send. `GET /messages`
and `POST /messages/acks` have the same request shapes and the same error
responses as before.

## Client checklist

1. Gate *showing* on the seen-set, not on "it was in the payload".
2. Upsert the stored copy for every retained message in every payload.
3. Prune the inbox on each poll, using the stored `expires_at`.
4. Never delete a retained message merely because it stopped arriving.
5. Test the 15-minute case: leave the app open across two polls and confirm a
   retained message does not reappear as new.
