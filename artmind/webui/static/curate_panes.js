"use strict";

// Curate tab, panes 2-5 (conflicts, tables, graph health, mart) and queue switching.
// Relies on el(), api() (dashboard.js) and curPost(), curDomainEl, curStatusEl (curate.js).
// Every graph/corpus string goes through el()/textContent.

const cpPreviewEl = document.getElementById("curate-pane-preview");
const cpLoaded = {};

function cpClear() { cpPreviewEl.replaceChildren(); }

// Preview (dry run) → Confirm → real call; one pattern for every pane write.
async function cpWrite(path, body, label, onDone) {
  cpClear();
  try {
    const dry = await curPost(path, { ...body, dry_run: true });
    const effect = dry.effect || {};
    cpPreviewEl.appendChild(el("p", null, `${label}: ${effect.summary || JSON.stringify(dry).slice(0, 200)}`));
    if (effect.undo) cpPreviewEl.appendChild(el("p", "dash-note", `undo: ${effect.undo}`));
    const go = el("button", null, "Confirm");
    go.type = "button";
    go.addEventListener("click", async () => {
      go.disabled = true;
      try { await curPost(path, { ...body, dry_run: false }); cpClear(); await onDone(); }
      catch (err) { cpPreviewEl.appendChild(el("p", "dash-note", `Failed: ${err.message}`)); }
    });
    cpPreviewEl.appendChild(go);
  } catch (err) { cpPreviewEl.appendChild(el("p", "dash-note", `Preview failed: ${err.message}`)); }
}

function cpBtn(label, handler, secondary = true) {
  const b = el("button", secondary ? "btn-secondary" : null, label);
  b.type = "button";
  b.addEventListener("click", handler);
  return b;
}
function cpAskLink(id, text) { document.getElementById(id).href = `/?ask=${encodeURIComponent(text)}`; }
function cpNeedDomain(body, what) {
  body.replaceChildren(el("p", "dash-empty", `Pick a domain to see ${what}.`));
}

// ── conflicts ────────────────────────────────────────────────────────
async function cpLoadConflicts() {
  const body = document.getElementById("curate-conflicts-body");
  body.replaceChildren(el("p", "dash-note", "Loading…"));
  const q = new URLSearchParams({ status: document.getElementById("curate-conflict-status").value });
  if (curDomainEl.value) q.set("domain", curDomainEl.value);
  try {
    const { groups } = await api(`/api/curate/conflicts?${q}`);
    body.replaceChildren();
    const n = groups.reduce((a, g) => a + g.rows.length, 0);
    cpAskLink("curate-conflicts-ask", `Review the ${n} ${q.get("status")} conflicts${curDomainEl.value ? ` in ${curDomainEl.value}` : ""} and suggest resolve or dismiss with reasons`);
    if (!groups.length) body.appendChild(el("p", "dash-empty", "No conflicts."));
    for (const g of groups) {
      const det = el("details", "curate-group");
      det.open = true;
      det.appendChild(el("summary", null, `${g.aspect || "(no aspect)"} · ${g.rows.length}`));
      for (const r of g.rows) det.appendChild(cpConflictRow(r));
      body.appendChild(det);
    }
  } catch (err) { body.replaceChildren(el("p", "dash-note", `Failed to load: ${err.message}`)); }
}

function cpConflictRow(r) {
  const c = r.conflict;
  const wrap = el("div", "curate-row");
  wrap.appendChild(el("span", "curate-badge", c.severity || "?"));
  const main = el("div");
  main.appendChild(el("strong", null, (r.entities || []).map((e) => e.name).join(" / ")));
  main.appendChild(el("div", null, `A: ${c.claim_a || "—"}   B: ${c.claim_b || "—"}`));
  if (r.older_side_docs.length) {
    main.appendChild(el("div", "dash-note", "One side is an older version: record the supersession (plugin: “This note supersedes…”, or `artmind ingest supersede`), then dismiss."));
  }
  for (const e of r.evidence || []) {
    const docName = e.document ? (e.document.name || e.document.path) : e.doc_id;
    main.appendChild(el("div", "curate-evidence", `[${e.side || "?"}] ${docName}: ${(e.text || "").slice(0, 400)}`));
  }
  if (c.status === "open") {
    const reason = el("input");
    reason.type = "text"; reason.placeholder = "reason (required)";
    main.appendChild(reason);
    for (const status of ["resolved", "dismissed"]) {
      main.appendChild(cpBtn(status === "resolved" ? "Resolve…" : "Dismiss…", () => {
        if (!reason.value.trim()) { curStatusEl.textContent = "Give a reason."; return; }
        cpWrite(`/api/curate/conflicts/${encodeURIComponent(c.id)}/close`, { status, reason: reason.value.trim() },
          `${status} conflict`, cpLoadConflicts);
      }));
    }
  } else main.appendChild(el("div", "dash-note", `${c.status}`));
  wrap.appendChild(main);
  return wrap;
}

// ── tables ───────────────────────────────────────────────────────────
async function cpLoadTables() {
  const body = document.getElementById("curate-tables-body");
  if (!curDomainEl.value) return cpNeedDomain(body, "tables");
  body.replaceChildren(el("p", "dash-note", "Loading…"));
  try {
    const data = await api(`/api/curate/tables?domain=${encodeURIComponent(curDomainEl.value)}`);
    body.replaceChildren();
    if (!data.tables.length) body.appendChild(el("p", "dash-empty", "Every table in this domain is adjudicated."));
    for (const t of data.tables) body.appendChild(cpTableNode(t));
  } catch (err) { body.replaceChildren(el("p", "dash-note", `Failed to load: ${err.message}`)); }
}

function cpTableNode(t) {
  const det = el("details", "curate-group");
  det.open = true;
  det.appendChild(el("summary", null, `${t.table} · grain ${t.grain || "?"}${t.grain_confirmed ? " (confirmed)" : ""}`));
  const base = `/api/curate/tables/${encodeURIComponent(t.table)}`;
  const common = { domain: t.domain };
  const seen = new Set();
  const acceptBtn = cpBtn("Accept all proposed…", async () => {
    for (const m of t.mappings) await curPost(`${base}/mapping`, { ...common, action: "confirm", column: m.column, entity_class: m.entity_class, dry_run: false });
    await cpLoadTables();
  });
  acceptBtn.disabled = t.mappings.length > 0;  // only after every row is expanded once
  const sample = (col) => (t.samples[col] || []).slice(0, 8).join(", ") || "no sample";
  for (const m of t.mappings) {
    const row = el("div", "curate-row");
    row.appendChild(el("span", "curate-badge", "mapping"));
    const main = el("div");
    main.appendChild(el("strong", null, `${m.column} → ${m.entity_class}`));
    main.appendChild(el("div", "curate-evidence", `values: ${sample(m.column)}`));
    seen.add(m.column);
    main.appendChild(cpBtn("Confirm…", () => cpWrite(`${base}/mapping`, { ...common, action: "confirm", column: m.column, entity_class: m.entity_class }, "confirm", cpLoadTables)));
    main.appendChild(cpBtn("Clear…", () => cpWrite(`${base}/mapping`, { ...common, action: "clear", column: m.column }, "clear", cpLoadTables)));
    row.appendChild(main);
    det.appendChild(row);
    if (seen.size === new Set(t.mappings.map((x) => x.column)).size) acceptBtn.disabled = false;
  }
  for (const b of t.bridge_columns) {
    const row = el("div", "curate-row");
    row.appendChild(el("span", "curate-badge", "bridge"));
    const main = el("div");
    main.appendChild(el("strong", null, b.column));
    main.appendChild(el("div", "curate-evidence", `values: ${sample(b.column)}`));
    main.appendChild(cpBtn("Confirm…", () => cpWrite(`${base}/bridge`, { ...common, action: "confirm", column: b.column }, "confirm bridge", cpLoadTables)));
    main.appendChild(cpBtn("Clear…", () => cpWrite(`${base}/bridge`, { ...common, action: "clear", column: b.column }, "clear bridge", cpLoadTables)));
    row.appendChild(main);
    det.appendChild(row);
  }
  const foot = el("div", "dash-form");
  if (!t.grain_confirmed) {
    const grain = el("select");
    for (const g of ["instance", "lookup", "normative"]) grain.appendChild(el("option", null, g));
    if (t.grain) grain.value = t.grain;
    foot.appendChild(grain);
    foot.appendChild(cpBtn("Confirm grain…", () => cpWrite(`${base}/grain`, { ...common, grain: grain.value }, "grain", cpLoadTables)));
  }
  if (t.mappings.length) foot.appendChild(acceptBtn);
  const missed = el("input"); missed.type = "text"; missed.placeholder = "column";
  const cls = el("input"); cls.type = "text"; cls.placeholder = "entity class";
  foot.append(missed, cls, cpBtn("Set a missed class…", () => {
    if (!missed.value.trim() || !cls.value.trim()) return;
    cpWrite(`${base}/mapping`, { ...common, action: "set", column: missed.value.trim(), entity_class: cls.value.trim() }, "set class", cpLoadTables);
  }));
  det.appendChild(foot);
  return det;
}

// ── graph health: the actions on what Insights found (findings are read in Explore → Insights) ──
const CP_SAMEAS = ["same_as_candidates", "split_signal"];
const CP_REEXTRACT = ["fragments", "attribution_suspects"];

async function cpLoadHealth() {
  const body = document.getElementById("curate-health-body");
  if (!curDomainEl.value) return cpNeedDomain(body, "actions");
  body.replaceChildren(el("p", "dash-note", "Loading…"));
  const domain = curDomainEl.value;
  try {
    const d = await api(`/api/insights?domain=${encodeURIComponent(domain)}`);
    const health = d.findings.filter((f) => f.kind === "health");
    body.replaceChildren(el("p", "dash-note", `snapshot ${d.created_at} · ${health.length} health finding(s). Read them in Explore → Insights; act on them here.`));
    const byDetector = new Map();
    for (const f of health) { if (!byDetector.has(f.detector)) byDetector.set(f.detector, []); byDetector.get(f.detector).push(f); }
    if (!health.length) body.appendChild(el("p", "dash-empty", "No health findings."));
    for (const [detector, list] of byDetector) body.appendChild(cpHealthGroup(domain, detector, list));
  } catch (err) { body.replaceChildren(el("p", "dash-note", `${err.message}`)); }
}

function cpHealthGroup(domain, detector, list) {
  const row = el("div", "curate-row");
  row.appendChild(el("span", "curate-badge", `${list.length}`));
  const main = el("div");
  main.appendChild(el("strong", null, detector));
  const open = cpBtn("Read in Insights", () => { document.querySelector('.tab[data-tab="insights"]').click(); });
  if (CP_SAMEAS.includes(detector)) {
    main.appendChild(el("div", "dash-note", "Proposes same-as groups for the Same-as queue (LLM calls; nothing is merged until you approve there)."));
    const send = cpBtn(`Send ${list.length} to the same-as queue…`, async () => {
      if (!window.confirm(`Propose same-as groups from ${list.length} finding(s)? This calls the LLM; nothing is merged yet.`)) return;
      send.disabled = true;
      try {
        const res = await curPost("/api/insights/same-as/propose", { domain, keys: list.map((f) => f.key) });
        cpPreviewEl.replaceChildren(el("p", null, `Proposed: ${JSON.stringify(res.stats)}; skipped ${(res.skipped || []).length}. Open the Same-as pane to review.`));
      } catch (err) { cpPreviewEl.replaceChildren(el("p", "dash-note", `Failed: ${err.message}`)); }
      finally { send.disabled = false; }
    });
    main.appendChild(send);
  } else if (CP_REEXTRACT.includes(detector)) {
    main.appendChild(el("div", "curate-evidence", `Evidence is in Insights. To redo a source note: artmind ingest sync "<path>" --reextract`));
  } else {
    main.appendChild(el("div", "dash-note", "A schema-level finding: edit the schema (Reference → Schemas shows it), then re-extract."));
  }
  main.appendChild(open);
  row.appendChild(main);
  return row;
}

// Insights cards call this: open Curate on the health pane for a domain.
window.cpOpenHealth = function (domain) {
  curDomainEl.value = domain;
  document.querySelector('.tab[data-tab="curate"]').click();
  cpShowQueue("health");
};

// ── mart ─────────────────────────────────────────────────────────────
async function cpLoadMart() {
  const body = document.getElementById("curate-mart-body");
  if (!curDomainEl.value) return cpNeedDomain(body, "the mart");
  body.replaceChildren(el("p", "dash-note", "Loading…"));
  try {
    const d = await api(`/api/curate/mart?domain=${encodeURIComponent(curDomainEl.value)}`);
    body.replaceChildren(el("pre", "curate-evidence", JSON.stringify(d.status, null, 1).slice(0, 1500)));
    for (const r of d.rows) body.appendChild(cpMartRow(r));
    if (!d.rows.length) body.appendChild(el("p", "dash-empty", "No unreviewed proposals."));
    body.appendChild(cpBtn("Rebuild mart…", () => cpWrite("/api/curate/mart/build", { domain: curDomainEl.value }, "rebuild", cpLoadMart)));
  } catch (err) { body.replaceChildren(el("p", "dash-note", `${err.message}`)); }
}

function cpMartRow(r) {
  const row = el("div", "curate-row");
  row.appendChild(el("span", "curate-badge", r.dimension || "member"));
  const main = el("div");
  const key = r.member_key || r.key || "";
  main.appendChild(el("strong", null, key));
  const levels = r.levels || {};
  const inputs = Object.keys(levels).map((k) => {
    const i = el("input"); i.type = "text"; i.value = levels[k] ?? ""; i.dataset.level = k; i.title = k;
    return i;
  });
  inputs.forEach((i) => main.appendChild(i));
  main.appendChild(cpBtn("Set levels…", () => {
    const lv = {};
    for (const i of inputs) lv[i.dataset.level] = i.value;
    cpWrite("/api/curate/mart/member", { domain: curDomainEl.value, dimension: r.dimension, member: key, levels: lv }, "set levels", cpLoadMart);
  }));
  row.appendChild(main);
  return row;
}

// ── queue switching + deep link ──────────────────────────────────────
const CP_LOADERS = { conflicts: cpLoadConflicts, tables: cpLoadTables, health: cpLoadHealth, mart: cpLoadMart, sameas: () => curLoadSameAs() };

function cpShowQueue(queue) {
  if (!CP_LOADERS[queue]) queue = "sameas";
  for (const b of document.querySelectorAll("#curate-queues .tab")) b.classList.toggle("active", b.dataset.queue === queue);
  for (const p of document.querySelectorAll("[data-queue-pane]")) p.hidden = p.dataset.queuePane !== queue;
  cpClear();
  CP_LOADERS[queue]();
}
function cpCurrentQueue() {
  const a = document.querySelector("#curate-queues .tab.active");
  return a ? a.dataset.queue : "sameas";
}

document.getElementById("curate-queues").addEventListener("click", (e) => {
  const b = e.target.closest(".tab[data-queue]");
  if (b) cpShowQueue(b.dataset.queue);
});
document.getElementById("curate-conflict-status").addEventListener("change", cpLoadConflicts);
document.getElementById("curate-push-catalogue").addEventListener("click", () => {
  if (!curDomainEl.value) { curStatusEl.textContent = "Pick a domain first."; return; }
  cpWrite("/api/curate/catalogue", { domain: curDomainEl.value }, "push the catalogue to the graph", cpLoadTables);
});
curDomainEl.addEventListener("change", () => { if (cpCurrentQueue() !== "sameas") cpShowQueue(cpCurrentQueue()); });
document.getElementById("curate-reload").addEventListener("click", () => { if (cpCurrentQueue() !== "sameas") cpShowQueue(cpCurrentQueue()); });

// ?queue= : open that queue once the tab has loaded its domains.
window.addEventListener("DOMContentLoaded", () => {
  const q = new URLSearchParams(window.location.search).get("queue");
  if (!q) return;
  setTimeout(() => cpShowQueue(q), 900);
});
