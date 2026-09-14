"use strict";

// Broadcast messages: list, compose, preview, audience.
//
// Everything here goes through /admin-api/*, which this app proxies to the
// messages service so the admin key stays server-side. Two things are
// deliberately NOT done in this file: markdown is parsed by the server and
// rendered from the tree it returns, and timestamps are converted between
// London and UTC by the server. One grammar, one timezone boundary.

const $ = (sel) => document.querySelector(sel);

// The compose form is either creating (null) or editing a loaded message.
let editing = null;

// ---- helpers ----------------------------------------------------------------

function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function debounce(fn, ms) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

async function api(path, options = {}) {
  const res = await fetch(SCRIPT_ROOT + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = res.status === 204 ? {} : await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(payload.error || `${path} -> ${res.status}`);
  return payload;
}

// "2026-10-01 09:00" (what the server renders) -> a datetime-local value.
function toInputValue(local) {
  return local ? local.replace(" ", "T") : "";
}

function numberOrNull(sel) {
  const raw = $(sel).value.trim();
  return raw === "" ? null : parseInt(raw, 10);
}

function textOrNull(sel) {
  const raw = $(sel).value.trim();
  return raw === "" ? null : raw;
}

function selectedOS() {
  return [...$("#f-os-panel").querySelectorAll("input:checked")].map((cb) => cb.value);
}

// ---- the list ---------------------------------------------------------------

const COLUMNS = [
  { key: "title", label: "Title" },
  { key: "display", label: "Display" },
  { key: "level", label: "Level" },
  { key: "priority", label: "Pri", num: true },
  { key: "window", label: "Window (London)" },
  { key: "enabled", label: "On" },
  { key: "status", label: "Status" },
  { key: "revision", label: "Rev", num: true },
  { key: "ack_count", label: "Acks", num: true },
  { key: "matched_client_count", label: "Audience", num: true },
];

function windowText(row) {
  return `${row.starts_at_local || "—"} → ${row.ends_at_local || "open"}`;
}

// Where a message is in its life, right now. "On" only says the kill switch has
// not been thrown; this is the column that says whether anything is happening.
//
// Computed from the raw UTC fields, never the London-rendered *_local ones —
// Date parses the offset, so the comparison is exact wherever the browser is.
//
// Precedence matters in one case. Expiring a retained message is done by pulling
// expires_at back *inside* the delivery window, so for one poll cycle it is both
// live and expired. Expired wins: it is the more useful fact, and it is still
// being delivered precisely to tell clients it has gone.
function messageStatus(row) {
  const now = Date.now();
  const at = (value) => (value ? new Date(value).getTime() : null);

  if (!row.enabled) {
    return { key: "off", label: "off", title: "Disabled. Reaching nobody." };
  }

  const expires = at(row.expires_at);
  if (row.retain && expires !== null && now >= expires) {
    return {
      key: "expired",
      label: "expired",
      title: "Past its expiry. Clients that polled since should have dropped it.",
    };
  }

  const starts = at(row.starts_at);
  if (starts !== null && now < starts) {
    return { key: "scheduled", label: "scheduled", title: "Not started yet." };
  }

  const ends = at(row.ends_at);
  if (ends === null || now < ends) {
    return { key: "live", label: "live", title: "Being delivered now." };
  }

  return row.retain
    ? {
        key: "kept",
        label: "kept",
        title: "Past its window, so no longer delivered — but still in inboxes " +
          "that hold it, until it expires.",
      }
    : { key: "ended", label: "ended", title: "Past its window. Reaching nobody." };
}

function cell(row, col) {
  if (col.key === "window") return esc(windowText(row));
  if (col.key === "status") {
    const state = messageStatus(row);
    return `<span class="pill st-${state.key}" title="${esc(state.title)}">${esc(
      state.label
    )}</span>`;
  }
  if (col.key === "enabled") {
    return row.enabled
      ? '<span class="pill on">on</span>'
      : '<span class="pill off">off</span>';
  }
  const value = row[col.key];
  if (value === null || value === undefined || value === "") {
    return '<span class="muted">—</span>';
  }
  return esc(value);
}

async function renderList() {
  const table = $("#table-messages");
  const note = $("#messages-note");

  let rows;
  try {
    ({ messages: rows } = await api("/admin-api/messages"));
  } catch (err) {
    table.innerHTML = "";
    note.textContent = err.message;
    return;
  }

  if (!rows.length) {
    table.innerHTML = "";
    note.textContent = "No messages yet.";
    return;
  }
  note.textContent = "";

  const thead = COLUMNS.map((c) => `<th>${c.label}</th>`).join("") + "<th></th>";
  const tbody = rows
    .map((row) => {
      const cells = COLUMNS.map(
        (c) => `<td class="${c.num ? "num" : ""}">${cell(row, c)}</td>`
      ).join("");
      // Revoke is offered only where it does something Disable cannot: a
      // message the client filed away. Everything else is shown once and
      // dropped, so there is nothing left on the device to withdraw.
      const revoke = row.retain
        ? `<button data-act="revoke" data-id="${esc(row.message_id)}" class="secondary">Revoke</button>`
        : "";
      const actions =
        `<td class="row-actions">` +
        `<button data-act="edit" data-id="${esc(row.message_id)}" class="secondary">Edit</button>` +
        revoke +
        `<button data-act="toggle" data-id="${esc(row.message_id)}" class="secondary">${
          row.enabled ? "Disable" : "Enable"
        }</button>` +
        `<button data-act="delete" data-id="${esc(row.message_id)}" class="secondary">Delete</button>` +
        `</td>`;
      return `<tr>${cells}${actions}</tr>`;
    })
    .join("");

  table.innerHTML = `<thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody>`;
  table.querySelectorAll("button[data-act]").forEach((button) => {
    button.addEventListener("click", () => rowAction(button.dataset.act, button.dataset.id));
  });
}

async function rowAction(action, messageId) {
  const row = (await api("/admin-api/messages")).messages.find(
    (m) => m.message_id === messageId
  );
  if (!row) return renderList();

  if (action === "edit") return openCompose(row);

  if (action === "toggle") {
    await api(`/admin-api/messages/${messageId}/enabled`, {
      method: "POST",
      body: JSON.stringify({ enabled: !row.enabled }),
    });
    return renderList();
  }

  if (action === "revoke") {
    // The one row action with preconditions the operator can fail — the
    // message has to still be going out, or the withdrawal reaches nobody. The
    // service decides that and says why, so its answer needs somewhere to land.
    if (
      !confirm(
        `Revoke "${row.title}"? Clients drop it from their inbox on their next ` +
          `poll. Leave it enabled until then — a revocation travels inside the ` +
          `message, so disabling it now would strand it in every inbox holding it.`
      )
    ) {
      return;
    }
    try {
      await api(`/admin-api/messages/${messageId}/expire`, { method: "POST" });
    } catch (err) {
      $("#messages-note").textContent = err.message;
      return;
    }
    return renderList();
  }

  if (action === "delete") {
    // Deleting takes the acks with it, so a message re-created with the same
    // wording would show again to everyone. Worth a confirmation.
    if (!confirm(`Delete "${row.title}"? This also deletes its ${row.ack_count} ack(s).`)) {
      return;
    }
    await api(`/admin-api/messages/${messageId}`, { method: "DELETE" });
    return renderList();
  }
}

// ---- compose ----------------------------------------------------------------

function openCompose(row) {
  editing = row || null;
  $("#compose").hidden = false;
  $("#compose-title").textContent = row ? "Edit message" : "New message";
  $("#compose-error").textContent = "";
  $("#bump-row").hidden = !row;
  $("#f-bump-revision").checked = false;

  $("#f-title").value = row ? row.title : "";
  $("#f-banner-title").value = row && row.banner_title ? row.banner_title : "";
  $("#f-body").value = row ? row.body : "";
  $("#f-display").value = row ? row.display : "banner";
  $("#f-level").value = row ? row.level : "info";
  $("#f-priority").value = row ? row.priority : 0;
  $("#f-action-label").value = row && row.action_label ? row.action_label : "";
  $("#f-action-url").value = row && row.action_url ? row.action_url : "";
  $("#f-starts-at").value = row ? toInputValue(row.starts_at_local) : "";
  $("#f-ends-at").value = row ? toInputValue(row.ends_at_local) : "";
  $("#f-expires-at").value = row ? toInputValue(row.expires_at_local) : "";
  $("#f-retain").checked = row ? row.retain : false;
  $("#f-dismissable-at").value = row ? toInputValue(row.dismissable_at_local) : "";
  $("#f-dismissable-after").value = row && row.dismissable_after ? row.dismissable_after : "";
  updateGroupVisibility();
  $("#f-min-version").value = row && row.min_version ? row.min_version : "";
  $("#f-max-version").value = row && row.max_version ? row.max_version : "";
  $("#f-min-days").value = row && row.min_days_count !== null ? row.min_days_count : "";
  $("#f-max-days").value = row && row.max_days_count !== null ? row.max_days_count : "";
  $("#f-client-ids").value = row && row.client_ids ? row.client_ids.join(", ") : "";

  const wanted = new Set(row && row.os ? row.os : []);
  $("#f-os-panel")
    .querySelectorAll("input")
    .forEach((cb) => (cb.checked = wanted.has(cb.value)));
  updateOSLabel();

  refreshPreview();
  refreshAudience();
  renderTimeline();
  $("#compose").scrollIntoView({ behavior: "smooth" });
}

function closeCompose() {
  editing = null;
  closeHelp();
  $("#compose").hidden = true;
}

function targetingPayload() {
  const clientIds = $("#f-client-ids")
    .value.split(",")
    .map((s) => s.trim())
    .filter(Boolean);

  return {
    client_ids: clientIds,
    os: selectedOS(),
    min_version: textOrNull("#f-min-version"),
    max_version: textOrNull("#f-max-version"),
    min_days_count: numberOrNull("#f-min-days"),
    max_days_count: numberOrNull("#f-max-days"),
  };
}

// The five time-valued fields sit in three groups with three owners, and no
// rule crosses between them:
//
//   Delivery     starts_at, ends_at        server: when it is sent
//   Retention    retain, expires_at        client: how long it is kept
//   Interaction  dismissable_*             client, banner: when it can be closed
//
// Two of the groups are conditional, and the service rejects rather than
// ignores their fields when they do not apply — so each group hides with its
// condition and sends explicit nulls when hidden. Otherwise a value left over
// from before the operator changed Display or Retain turns a save into a 400
// with no visible cause.
//
// `banner_title` is not a time, but it is banner-only and rejected the same
// way, so it rides the same pair of moves: hidden with the Display select, and
// nulled in the payload when it is. It is also *required* on a banner — the
// save fails with the service's own wording, which is the right place for it
// to fail: a second copy of the rule here is one that can disagree.

function isBanner() {
  return $("#f-display").value === "banner";
}

function isRetained() {
  return $("#f-retain").checked;
}

function updateGroupVisibility() {
  $("#banner-title-row").hidden = !isBanner();
  $("#dismissal-row").hidden = !isBanner();
  $("#expires-row").hidden = !isRetained();
}

function bannerPayload() {
  if (!isBanner()) {
    return { banner_title: null, dismissable_at: null, dismissable_after: null };
  }
  return {
    banner_title: textOrNull("#f-banner-title"),
    dismissable_at: textOrNull("#f-dismissable-at"),
    dismissable_after: textOrNull("#f-dismissable-after"),
  };
}

function retentionPayload() {
  return {
    retain: isRetained(),
    expires_at: isRetained() ? textOrNull("#f-expires-at") : null,
  };
}

function composePayload() {
  const payload = {
    title: $("#f-title").value.trim(),
    body: $("#f-body").value,
    display: $("#f-display").value,
    level: $("#f-level").value,
    priority: parseInt($("#f-priority").value, 10) || 0,
    starts_at: textOrNull("#f-starts-at"),
    ends_at: textOrNull("#f-ends-at"),
    action_url: textOrNull("#f-action-url"),
    action_label: textOrNull("#f-action-label"),
    ...retentionPayload(),
    ...bannerPayload(),
    ...targetingPayload(),
  };

  // A PUT is a full rewrite, so an omitted `enabled` would silently re-enable
  // a message that was disabled from the list. Carry the current value.
  if (editing) {
    payload.enabled = editing.enabled;
    payload.bump_revision = $("#f-bump-revision").checked;
  }
  return payload;
}

async function save() {
  const error = $("#compose-error");
  error.textContent = "";
  try {
    if (editing) {
      await api(`/admin-api/messages/${editing.message_id}`, {
        method: "PUT",
        body: JSON.stringify(composePayload()),
      });
    } else {
      await api("/admin-api/messages", {
        method: "POST",
        body: JSON.stringify(composePayload()),
      });
    }
  } catch (err) {
    error.textContent = err.message;
    return;
  }
  closeCompose();
  renderList();
}

// ---- live preview -----------------------------------------------------------

function renderSpan(span) {
  const text = esc(span.text);
  if (span.kind === "bold") return `<strong>${text}</strong>`;
  if (span.kind === "italic") return `<em>${text}</em>`;
  if (span.kind === "link") {
    return `<a href="${esc(span.url)}" target="_blank" rel="noopener">${text}</a>`;
  }
  return text;
}

function renderBlocks(blocks) {
  return blocks
    .map((block) => {
      const items = block.items.map((spans) => spans.map(renderSpan).join(""));
      if (block.kind === "bullets") {
        return `<ul>${items.map((item) => `<li>${item}</li>`).join("")}</ul>`;
      }
      return `<p>${items[0]}</p>`;
    })
    .join("");
}

async function refreshPreview() {
  $("#preview-title").textContent = $("#f-title").value;

  const label = $("#f-action-label").value.trim();
  $("#preview-action").hidden = !label;
  $("#preview-action").textContent = label;

  const body = $("#f-body").value;
  const target = $("#preview-body");
  const error = $("#preview-error");

  if (!body.trim()) {
    target.innerHTML = "";
    error.textContent = "";
    return;
  }

  try {
    const { blocks } = await api("/admin-api/preview", {
      method: "POST",
      body: JSON.stringify({ body }),
    });
    target.innerHTML = renderBlocks(blocks);
    error.textContent = "";
  } catch (err) {
    // The body is mid-keystroke most of the time, so a parse failure is
    // normal. Keep the last good render and say what is wrong underneath.
    error.textContent = err.message;
  }
}

// ---- timeline ---------------------------------------------------------------

// A strip showing where each of the four moments falls relative to the others,
// so the operator can see the shape rather than reconstruct it from four
// separate inputs. It is a picture of what was typed, not a validity check —
// no arrangement of these is illegal any more.
//
// The values are read straight from the datetime-local inputs and parsed in the
// browser's own timezone. That is fine here because all four are London
// wall-clock and only their *relative* positions are drawn, so a common offset
// cancels. A pair spanning a clock change is out by an hour, which at any
// realistic scale is well under a pixel.

const TIMELINE_MARKS = [
  { sel: "#f-starts-at", key: "start", label: "Starts", group: "delivery" },
  { sel: "#f-ends-at", key: "end", label: "Ends", group: "delivery" },
  { sel: "#f-expires-at", key: "expires", label: "Expires", group: "retention" },
  { sel: "#f-dismissable-at", key: "dismiss", label: "Dismissable", group: "interaction" },
];

function timelineValues() {
  return TIMELINE_MARKS.filter((mark) => {
    if (mark.group === "retention" && !isRetained()) return false;
    if (mark.group === "interaction" && !isBanner()) return false;
    return Boolean($(mark.sel).value);
  }).map((mark) => ({ ...mark, at: new Date($(mark.sel).value).getTime() }))
    .filter((mark) => Number.isFinite(mark.at));
}

function renderTimeline() {
  const marks = timelineValues();
  const key = $("#tl-key");
  const empty = $("#tl-empty");
  const span = $("#tl-span");

  if (!marks.length) {
    key.innerHTML = "";
    span.style.width = "0%";
    empty.hidden = false;
    return;
  }
  empty.hidden = true;

  // One point, or several at the same instant, has no extent to scale to.
  const times = marks.map((m) => m.at);
  const low = Math.min(...times);
  const high = Math.max(...times);
  const at = (t) => (high === low ? 50 : ((t - low) / (high - low)) * 100);

  const start = marks.find((m) => m.key === "start");
  const end = marks.find((m) => m.key === "end");
  if (start) {
    const from = at(start.at);
    // No end means delivery runs off the right-hand side; fill to the edge.
    span.style.left = `${from}%`;
    span.style.width = `${(end ? at(end.at) : 100) - from}%`;
  } else {
    span.style.left = "0%";
    span.style.width = end ? `${at(end.at)}%` : "0%";
  }

  key.innerHTML = marks
    .slice()
    .sort((a, b) => a.at - b.at)
    .map(
      (mark) =>
        `<li class="tl-${mark.group}"><span class="tl-dot"></span>` +
        `${esc(mark.label)} <span class="muted">${esc(
          $(mark.sel).value.replace("T", " ")
        )}</span></li>`
    )
    .join("");
}

// ---- live audience ----------------------------------------------------------

async function refreshAudience() {
  const target = $("#audience");
  try {
    const { count } = await api("/admin-api/audience", {
      method: "POST",
      body: JSON.stringify(targetingPayload()),
    });
    target.textContent = `${count} client${count === 1 ? "" : "s"}`;
  } catch (err) {
    target.textContent = err.message;
  }
}

// ---- field help -------------------------------------------------------------

// What each non-obvious compose field actually does, keyed by the `data-help`
// on its "?" button. These are static, author-written strings — the only HTML
// in this file that is not escaped, and the only place that should stay true.
// The rules they describe live in the messages service (`targeting.py`,
// `models.py`, `docs/messages-client-guide.md`); change them together.
const HELP = {
  body: [
    "Body",
    "A closed markdown subset: <code>**bold**</code>, <code>*italic*</code> and " +
      "<code>[label](https://…)</code>, with no nesting. The service rejects anything " +
      "else on save, so the app can parse it without guessing.",
    "Blank lines split blocks. A block whose <em>every</em> line starts with " +
      "<code>- </code> is a bullet list; otherwise the lines join into one paragraph " +
      "with a space — which is why a list needs a blank line above it. Max 2000 " +
      "characters; the preview shows exactly what the app will draw.",
  ],
  display: [
    "Display",
    "<strong>Modal</strong> — a dialog, at most one per app foreground. If several " +
      "match, the highest priority becomes the modal and the rest go to the inbox.",
    "<strong>Banner</strong> — inline on the schedule screen. " +
      "<strong>Inbox</strong> — listed only, never interrupts.",
  ],
  level: [
    "Level",
    "Styling only. A warning is drawn more prominently; it is not more blocking, " +
      "and it changes nothing about who gets the message or when.",
  ],
  priority: [
    "Priority",
    "Sort order for a client holding several messages: highest first, then newest. " +
      "It decides which one becomes that client's single modal. Any integer, " +
      "negatives included; 0 is fine unless something must jump the queue.",
  ],
  action_label: [
    "Action label",
    "The text on the button. Both-or-neither with Action URL — fill in both, or " +
      "leave both blank and the message has no button. Max 30 characters.",
  ],
  action_url: [
    "Action URL",
    "Where the button opens. Must start with <code>https://</code>. Ignored unless " +
      "Action label is filled in too.",
  ],
  starts_at: [
    "Starts",
    "When the message goes live, inclusive. Leave it blank to start now — that is " +
      "the usual case for a closure notice you are writing as it happens.",
  ],
  ends_at: [
    "Ends",
    "When it stops being served, exclusive: a message ending at the moment another " +
      "starts will not overlap it. Blank leaves it running until you disable it.",
  ],
  expires_at: [
    "Expires",
    "When a retained message should leave the client's local inbox. Blank means " +
      "never. Retention only — it has no relationship to Ends or to the " +
      "dismissal times, and is rejected outright without “Keep in the inbox”.",
    "Setting it to now is how you clear a message from inboxes that already " +
      "hold it — but <strong>Revoke</strong> on the message's row does exactly " +
      "that, and cannot get the details wrong. Retained messages keep being " +
      "sent, so clients pick the new value up on their next poll, within 15 " +
      "minutes; do not bump the revision (that re-shows it) or pull Ends back " +
      "(that strands it in every inbox holding it).",
  ],
  retain: [
    "Keep in the inbox",
    "Whether the client files the message in its local inbox after showing it, so " +
      "the user can find it again. Off means show it once and forget it.",
    "It has nothing to do with re-sending: a client stops receiving a message as " +
      "soon as it acks it, retained or not.",
  ],
  os: [
    "OS",
    "Nothing checked means every platform — the usual case. Once you check some, a " +
      "client that reports no OS at all is excluded.",
  ],
  min_version: [
    "Min version",
    "Lowest app version to serve, inclusive. Compared numerically, so 1.0.10 is " +
      "above 1.0.9, and 1.0 equals 1.0.0.",
    "Set either bound and clients on an unparseable version (<code>1.2.3-beta</code>) " +
      "or none at all drop out. With neither bound set they are included.",
  ],
  max_version: [
    "Max version",
    "Highest app version to serve, inclusive — e.g. to tell people on an old build " +
      "to update. Blank for no upper bound.",
  ],
  min_days: [
    "Min days seen",
    "How many days the client has used the app, inclusive — the count upstream-api " +
      "keeps. Use it to reach established users.",
    "A client we have never seen counts as 1, so anything above 1 excludes fresh " +
      "installs as well as genuinely new ones.",
  ],
  max_days: [
    "Max days seen",
    "The other end of the same count, inclusive. <code>1</code> is the welcome " +
      "message: brand-new installs only.",
  ],
  client_ids: [
    "Client IDs",
    "Comma-separated client UUIDs — the way to try a message on your own device " +
      "before it goes out. Blank means everyone matching the other rules.",
  ],
  banner_title: [
    "Banner title",
    "What the banner itself says, in the one line it has — the Title and Body " +
      "are what the user reads after tapping it. <strong>Required on a " +
      "banner</strong>, and rejected on a modal or an inbox message, which " +
      "have nowhere to draw it.",
    "Write the short one here and the full one in Title: <em>Closed today</em> " +
      "on the schedule screen, <em>Lagoon closed for maintenance until 6pm</em> " +
      "when they open it. It is required precisely so a banner does not end up " +
      "showing a title written for a dialog.",
    "Same 100-character limit as the Title, but the point is to be shorter. " +
      "The Preview shows the tapped-through message, not the banner line.",
  ],
  dismissable_at: [
    "Dismissable at",
    "Banners only. Until this moment the user cannot dismiss the banner — it " +
      "stays on the schedule screen with no way to close it. Blank means " +
      "dismissable straight away.",
    "Interaction only. It does not have to sit inside Delivery: a Dismissable " +
      "at past Ends is a banner nobody can close for its whole life, which is " +
      "allowed.",
  ],
  dismissable_after: [
    "Dismissable after",
    "The same lock, counted from when the client first shows the banner rather " +
      "than from a fixed time — <code>30s</code>, <code>5m</code>, " +
      "<code>2h</code>. Use it when what matters is that the message was on " +
      "screen, not when. No upper limit; Ends stops it being served anyway.",
    "Set both and the <em>earlier</em> one wins: whichever comes first unlocks " +
      "the banner. So a short “after” will usually override a later “at”.",
  ],
  bump_revision: [
    "Re-show to clients who have already seen it",
    "Editing a message does not bring it back: a client that acked it stays quiet. " +
      "Ticking this bumps the revision, which re-serves it to everyone — how a " +
      "corrected closure notice reaches the people who saw the wrong one.",
  ],
};

let helpBox = null;
let helpOpenFor = null;

function closeHelp() {
  if (!helpOpenFor) return;
  helpOpenFor.setAttribute("aria-expanded", "false");
  helpOpenFor = null;
  if (helpBox) helpBox.hidden = true;
}

function openHelp(button) {
  const entry = HELP[button.dataset.help];
  if (!entry) return;
  const [heading, ...paragraphs] = entry;

  if (!helpBox) {
    helpBox = document.createElement("div");
    helpBox.className = "help-box";
    helpBox.id = "help-box";
    helpBox.setAttribute("role", "tooltip");
    document.body.appendChild(helpBox);
  }
  helpBox.innerHTML =
    `<h4>${heading}</h4>` + paragraphs.map((text) => `<p>${text}</p>`).join("");
  helpBox.hidden = false;

  // Page coordinates, clamped to the viewport: the box is wider than most of
  // the fields it belongs to, and the right-hand column would push it offscreen.
  const rect = button.getBoundingClientRect();
  const margin = 8;
  const maxLeft =
    window.scrollX + document.documentElement.clientWidth - helpBox.offsetWidth - margin;
  const left = Math.max(window.scrollX + margin, Math.min(window.scrollX + rect.left, maxLeft));
  helpBox.style.left = `${left}px`;
  helpBox.style.top = `${window.scrollY + rect.bottom + 6}px`;

  button.setAttribute("aria-expanded", "true");
  helpOpenFor = button;
}

function wireHelp() {
  document.querySelectorAll("button.help").forEach((button) => {
    const entry = HELP[button.dataset.help];
    button.setAttribute("aria-expanded", "false");
    button.setAttribute("aria-describedby", "help-box");
    button.setAttribute("aria-label", entry ? `About ${entry[0]}` : "Help");
    button.addEventListener("click", (e) => {
      // Inside a <label>, an unhandled click would focus or toggle the field.
      e.preventDefault();
      e.stopPropagation();
      const wasOpen = helpOpenFor === button;
      closeHelp();
      if (!wasOpen) openHelp(button);
    });
  });

  document.addEventListener("click", (e) => {
    if (!helpBox || !helpBox.contains(e.target)) closeHelp();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeHelp();
  });
  // The box is positioned once, so anything that moves the field it points at
  // — the compose section opening, the window reflowing — closes it.
  window.addEventListener("resize", closeHelp);
}

// ---- wiring -----------------------------------------------------------------

function updateOSLabel() {
  const chosen = selectedOS();
  $("#f-os-toggle").textContent = chosen.length ? chosen.join(", ") : "All";
}

document.addEventListener("DOMContentLoaded", () => {
  $("#new-message").addEventListener("click", () => openCompose(null));
  $("#cancel-message").addEventListener("click", closeCompose);
  $("#save-message").addEventListener("click", save);

  const previewSoon = debounce(refreshPreview, 250);
  ["#f-title", "#f-body", "#f-action-label"].forEach((sel) =>
    $(sel).addEventListener("input", previewSoon)
  );

  const audienceSoon = debounce(refreshAudience, 250);
  ["#f-min-version", "#f-max-version", "#f-min-days", "#f-max-days", "#f-client-ids"].forEach(
    (sel) => $(sel).addEventListener("input", audienceSoon)
  );

  $("#f-os-toggle").addEventListener("click", (e) => {
    e.stopPropagation();
    $("#f-os-panel").hidden = !$("#f-os-panel").hidden;
  });
  $("#f-os-panel").addEventListener("change", () => {
    updateOSLabel();
    refreshAudience();
  });
  document.addEventListener("click", (e) => {
    if (!$("#f-os").contains(e.target)) $("#f-os-panel").hidden = true;
  });

  // "Vanish when the campaign ends" is the common case, and NULL-means-never
  // is the wrong default even though it is the right semantic. Only pre-fill
  // an untouched field, so an explicit choice is never overwritten.
  $("#f-ends-at").addEventListener("change", () => {
    if (!$("#f-expires-at").value) $("#f-expires-at").value = $("#f-ends-at").value;
    renderTimeline();
  });

  $("#f-display").addEventListener("change", () => {
    updateGroupVisibility();
    renderTimeline();
  });
  $("#f-retain").addEventListener("change", () => {
    updateGroupVisibility();
    renderTimeline();
  });
  [
    "#f-starts-at", "#f-ends-at", "#f-expires-at", "#f-dismissable-at",
  ].forEach((sel) => $(sel).addEventListener("change", renderTimeline));

  wireHelp();
  renderList();
});
