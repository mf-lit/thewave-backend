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
  const res = await fetch(path, {
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
  { key: "revision", label: "Rev", num: true },
  { key: "ack_count", label: "Acks", num: true },
  { key: "matched_client_count", label: "Audience", num: true },
];

function windowText(row) {
  return `${row.starts_at_local || "—"} → ${row.ends_at_local || "open"}`;
}

function cell(row, col) {
  if (col.key === "window") return esc(windowText(row));
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
      const actions =
        `<td class="row-actions">` +
        `<button data-act="edit" data-id="${esc(row.message_id)}" class="secondary">Edit</button>` +
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
  $("#compose").scrollIntoView({ behavior: "smooth" });
}

function closeCompose() {
  editing = null;
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

function composePayload() {
  const payload = {
    title: $("#f-title").value.trim(),
    body: $("#f-body").value,
    display: $("#f-display").value,
    level: $("#f-level").value,
    priority: parseInt($("#f-priority").value, 10) || 0,
    retain: $("#f-retain").checked,
    starts_at: textOrNull("#f-starts-at"),
    ends_at: textOrNull("#f-ends-at"),
    expires_at: textOrNull("#f-expires-at"),
    action_url: textOrNull("#f-action-url"),
    action_label: textOrNull("#f-action-label"),
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
  });

  renderList();
});
