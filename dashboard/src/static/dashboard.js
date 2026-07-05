"use strict";

// ---- shared controls --------------------------------------------------------
const $ = (sel) => document.querySelector(sel);

function controlParams() {
  const p = new URLSearchParams();
  if ($("#from").value) p.set("from", $("#from").value);
  if ($("#to").value) p.set("to", $("#to").value);
  p.set("granularity", $("#granularity").value);
  if ($("#exclude-cloud").checked) p.set("exclude_cloud", "1");
  return p;
}

function fmtDate(d) {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day}`;
}

// Default view: the last 7 days (today and the 6 days before it).
function applyDefaultDateRange() {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - 6);
  $("#from").value = fmtDate(from);
  $("#to").value = fmtDate(to);
}

async function getJSON(path, params) {
  const qs = params ? "?" + params.toString() : "";
  const res = await fetch(path + qs);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

// ---- summary badges ---------------------------------------------------------
// Fixed-window counts; independent of the date picker, but honour the cloud toggle.
async function renderSummary() {
  const params = new URLSearchParams();
  if ($("#exclude-cloud").checked) params.set("exclude_cloud", "1");
  const s = await getJSON("/api/summary", params);
  $("#stat-active").textContent = s.active_clients;
  $("#stat-new-week").textContent = s.new_this_week;
  $("#stat-new-yesterday").textContent = s.new_yesterday;
  $("#stat-new-today").textContent = s.new_today;
  $("#stat-all-today").textContent = s.all_today;
  $("#stat-all-yesterday").textContent = s.all_yesterday;
  $("#stat-all-week").textContent = s.all_week;
}

// ---- charts -----------------------------------------------------------------
const charts = {};

function renderBarChart(canvasId, label, data, color) {
  const ctx = document.getElementById(canvasId);
  const labels = data.map((d) => d.period);
  const counts = data.map((d) => d.count);
  if (charts[canvasId]) {
    charts[canvasId].data.labels = labels;
    charts[canvasId].data.datasets[0].data = counts;
    charts[canvasId].update();
    return;
  }
  charts[canvasId] = new Chart(ctx, {
    type: "bar",
    data: { labels, datasets: [{ label, data: counts, backgroundColor: color }] },
    options: {
      responsive: true,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#8b9aa7", maxRotation: 90 }, grid: { color: "#2a3947" } },
        y: { beginAtZero: true, ticks: { color: "#8b9aa7", precision: 0 }, grid: { color: "#2a3947" } },
      },
    },
  });
}

async function refreshCharts() {
  const params = controlParams();
  const [newClients, activeClients] = await Promise.all([
    getJSON("/api/clients/new", params),
    getJSON("/api/clients/active", params),
  ]);
  renderBarChart("chart-new", "New clients", newClients, "#2dd4bf");
  renderBarChart("chart-active", "Active clients", activeClients, "#60a5fa");
}

// ---- clients table ----------------------------------------------------------
const CLIENT_COLUMNS = [
  { key: "alias", label: "Alias", default: true },
  { key: "uuid", label: "UUID", default: false },
  { key: "first_seen", label: "First seen", default: true },
  { key: "last_seen", label: "Last seen", default: true },
  { key: "request_count", label: "Requests", default: true, num: true },
  { key: "days_count", label: "Days", default: true, num: true },
  { key: "client_os", label: "OS", default: true },
  { key: "client_version", label: "Version", default: true },
  { key: "first_ip", label: "First IP", default: false },
  { key: "last_ip", label: "Last IP", default: false },
];

// UI state for the single clients table: current sort, hidden columns, page offset.
const clientTable = {
  sort: "last_seen",
  dir: "desc",
  hidden: new Set(CLIENT_COLUMNS.filter((c) => !c.default).map((c) => c.key)),
  offset: 0,
};

function buildColumnToggles() {
  const container = document.querySelector('.col-toggles[data-table="clients"]');
  CLIENT_COLUMNS.forEach((col) => {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !clientTable.hidden.has(col.key);
    cb.addEventListener("change", () => {
      if (cb.checked) clientTable.hidden.delete(col.key);
      else clientTable.hidden.add(col.key);
      renderClientTable();
    });
    label.append(cb, document.createTextNode(" " + col.label));
    container.append(label);
  });
}

// Render a stored timestamp in the browser's local timezone as YYYY-MM-DD HH:MM:SS.
// Values with an offset (first_seen/last_seen carry +00:00) parse directly; naive
// values (e.g. notifications.created_at = utcnow) are treated as UTC.
function fmtLocal(value) {
  let s = String(value).trim();
  if (!/([zZ]|[+-]\d{2}:?\d{2})$/.test(s)) s += "Z";
  const d = new Date(s);
  if (isNaN(d.getTime())) return String(value);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function fmtCell(col, value) {
  if (value === null || value === undefined || value === "") {
    return '<span class="muted">—</span>';
  }
  if (col.key === "first_seen" || col.key === "last_seen") {
    return fmtLocal(value);
  }
  if (col.key === "first_ip" || col.key === "last_ip") {
    const ip = encodeURIComponent(String(value));
    return `<a href="https://whatismyipaddress.com/ip/${ip}" target="_blank" rel="noopener">${value}</a>`;
  }
  return String(value);
}

async function renderClientTable() {
  const limit = parseInt($("#limit").value, 10) || 0;
  const params = controlParams();
  params.set("sort", clientTable.sort);
  params.set("dir", clientTable.dir);
  params.set("limit", limit);
  params.set("offset", clientTable.offset);
  const { total, rows } = await getJSON("/api/clients", params);

  const cols = CLIENT_COLUMNS.filter((c) => !clientTable.hidden.has(c.key));
  const table = document.getElementById("table-clients");

  const thead = cols
    .map((c) => {
      let cls = "sortable";
      if (clientTable.sort === c.key) cls = clientTable.dir === "asc" ? "sort-asc" : "sort-desc";
      return `<th data-key="${c.key}" class="${cls}">${c.label}</th>`;
    })
    .join("");

  const tbody = rows
    .map(
      (r) =>
        "<tr>" +
        cols.map((c) => `<td class="${c.num ? "num" : ""}">${fmtCell(c, r[c.key])}</td>`).join("") +
        "</tr>"
    )
    .join("");

  table.innerHTML = `<thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody>`;
  table.querySelectorAll("th").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (clientTable.sort === key) clientTable.dir = clientTable.dir === "asc" ? "desc" : "asc";
      else { clientTable.sort = key; clientTable.dir = "asc"; }
      clientTable.offset = 0; // new sort starts at the first page
      renderClientTable();
    });
  });

  updatePager(total, rows.length, limit);
}

function updatePager(total, shown, limit) {
  const pager = document.querySelector(".pager");
  const paged = limit > 0;
  const start = total === 0 ? 0 : clientTable.offset + 1;
  const end = clientTable.offset + shown;

  pager.querySelector(".pager-status").textContent =
    total === 0 ? "No rows" : `${start}–${end} of ${total}`;

  pager.querySelector('[data-page="prev"]').disabled = !paged || clientTable.offset <= 0;
  pager.querySelector('[data-page="next"]').disabled = !paged || end >= total;
}

// ---- notifications table ----------------------------------------------------
const NOTIF_COLUMNS = [
  { key: "title", label: "Session" },
  { key: "date", label: "Date" },
  { key: "time", label: "Time" },
  { key: "side", label: "Side" },
  { key: "notification_type", label: "Type" },
  { key: "thresholds", label: "Thresholds", list: true },
  { key: "notified_thresholds", label: "Notified", list: true },
  { key: "last_checked_availability", label: "Last avail.", num: true },
  { key: "alias", label: "Alias" },
  { key: "client_id", label: "Client ID", trunc: 8 },
  { key: "created_at", label: "Created", ts: true },
];

// Fetched once; sorting and column visibility are client-side.
let notifRows = [];
const notifSort = { key: null, dir: "asc" };
const notifHidden = new Set();  // empty = all columns visible

function buildNotifColumnToggles() {
  const container = document.querySelector('.col-toggles[data-table="notifications"]');
  NOTIF_COLUMNS.forEach((col) => {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = !notifHidden.has(col.key);
    cb.addEventListener("change", () => {
      if (cb.checked) notifHidden.delete(col.key);
      else notifHidden.add(col.key);
      drawNotifications();
    });
    label.append(cb, document.createTextNode(" " + col.label));
    container.append(label);
  });
}

async function renderNotifications() {
  notifRows = await getJSON("/api/notifications");
  drawNotifications();
}

function cmpNotif(a, b, col) {
  let va = a[col.key], vb = b[col.key];
  if (col.num) return (va ?? -Infinity) - (vb ?? -Infinity);
  if (col.list) { va = (va || []).join(","); vb = (vb || []).join(","); }
  va = va == null ? "" : String(va);
  vb = vb == null ? "" : String(vb);
  return va.localeCompare(vb, undefined, { numeric: true });
}

function drawNotifications() {
  const table = document.getElementById("table-notifications");
  let rows = notifRows;
  if (notifSort.key) {
    const col = NOTIF_COLUMNS.find((c) => c.key === notifSort.key);
    const sign = notifSort.dir === "asc" ? 1 : -1;
    rows = [...notifRows].sort((a, b) => sign * cmpNotif(a, b, col));
  }

  const cols = NOTIF_COLUMNS.filter((c) => !notifHidden.has(c.key));
  const thead = cols.map((c) => {
    let cls = "sortable";
    if (notifSort.key === c.key) cls = notifSort.dir === "asc" ? "sort-asc" : "sort-desc";
    return `<th data-key="${c.key}" class="${cls}">${c.label}</th>`;
  }).join("");

  const tbody = rows
    .map((r) => {
      const cells = cols.map((c) => {
        let v = r[c.key];
        if (c.list) v = Array.isArray(v) && v.length ? v.join(", ") : "—";
        else if (v === null || v === undefined || v === "") v = '<span class="muted">—</span>';
        else if (c.ts) v = fmtLocal(v);
        else if (c.trunc) v = `<span title="${v}">${String(v).slice(0, c.trunc)}</span>`;
        return `<td class="${c.num ? "num" : ""}">${v}</td>`;
      }).join("");
      return `<tr>${cells}</tr>`;
    })
    .join("");

  table.innerHTML = `<thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody>`;
  table.querySelectorAll("th").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.key;
      if (notifSort.key === key) notifSort.dir = notifSort.dir === "asc" ? "desc" : "asc";
      else { notifSort.key = key; notifSort.dir = "asc"; }
      drawNotifications();
    });
  });
}

// ---- wiring -----------------------------------------------------------------
// Changing filters/limit invalidates the current page, so reset the table to page 0.
function refreshAll() {
  clientTable.offset = 0;
  renderSummary();
  refreshCharts();
  renderClientTable();
}

function wirePager() {
  const pager = document.querySelector(".pager");
  pager.addEventListener("click", (e) => {
    const dir = e.target.dataset.page;
    if (!dir) return;
    const limit = parseInt($("#limit").value, 10) || 0;
    if (limit <= 0) return;
    clientTable.offset =
      dir === "next" ? clientTable.offset + limit : Math.max(0, clientTable.offset - limit);
    renderClientTable();
  });
}

document.addEventListener("DOMContentLoaded", () => {
  buildColumnToggles();
  buildNotifColumnToggles();
  wirePager();
  $("#apply").addEventListener("click", refreshAll);
  $("#exclude-cloud").addEventListener("change", refreshAll); // toggle applies immediately
  $("#reset").addEventListener("click", () => {
    applyDefaultDateRange();
    $("#granularity").value = "day";
    $("#limit").value = "40";
    $("#exclude-cloud").checked = true;
    refreshAll();
  });
  applyDefaultDateRange();
  refreshAll();
  renderNotifications();
});
