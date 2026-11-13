"use strict";

// Curate tab, panes 2-5 (conflicts, tables, graph health, mart) and queue switching.
// Relies on el(), api() (dashboard.js) and curPost(), curDomainEl, curStatusEl (curate.js).
// Every graph/corpus string goes through el()/textContent.

const cpPreviewEl = document.getElementById("curate-pane-preview");
const cpLoaded = {};

function cpClear() { cpPreviewEl.replaceChildren(); }

// Low-risk write (confirming a proposal, saving text): no preview, a "Done" line afterwards.
async function cpApply(path, body, label, onDone) {
  cpClear();
  try {
    const res = await curPost(path, { ...body, dry_run: false });
    await onDone();
    cpPreviewEl.appendChild(el("p", null, `Done. ${(res.effect && res.effect.summary) || label}`));
  } catch (err) { cpPreviewEl.appendChild(el("p", "dash-note", `Failed: ${err.message}`)); }
}

// Preview (dry run) → Apply → real call, for writes that discard something.
async function cpWrite(path, body, label, onDone) {
  cpClear();
  try {
    const dry = await curPost(path, { ...body, dry_run: true });
    const effect = dry.effect || {};
    const said = `${label}: ${effect.summary || JSON.stringify(dry).slice(0, 200)}`;
    cpPreviewEl.appendChild(el("p", null, said));
    if (effect.undo) cpPreviewEl.appendChild(el("p", "dash-note", `undo: ${effect.undo}`));
    const go = el("button", null, "Apply");
    go.type = "button";
    go.addEventListener("click", async () => {
      go.disabled = true;
      try {
        await curPost(path, { ...body, dry_run: false });
        cpClear();
        await onDone();
        cpPreviewEl.appendChild(el("p", null, `Done. ${said}`));
      } catch (err) { cpPreviewEl.appendChild(el("p", "dash-note", `Failed: ${err.message}`)); }
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

// One table per structured table: a row per column with its proposed mapping, bridge role and
// description. Rows are selectable; one bulk action = one preview, one Confirm, one batch call.
function cpTd(content, cls) {
  const td = el("td", cls || null);
  if (content instanceof Node) td.appendChild(content); else td.textContent = content == null ? "" : String(content);
  return td;
}

function cpTableNode(t) {
  const det = el("details", "curate-group");
  det.open = true;
  det.appendChild(el("summary", null, `${t.table} · ${t.domain} · grain ${t.grain || "?"}${t.grain_confirmed ? " (confirmed)" : ""}`));
  const base = `/api/curate/tables/${encodeURIComponent(t.table)}`;
  const common = { domain: t.domain };
  const d = t.descriptions || { description: "", columns: {} };
  const maps = new Map();
  for (const m of t.mappings) { if (!maps.has(m.column)) maps.set(m.column, []); maps.get(m.column).push(m); }
  const bridges = new Map((t.bridge_columns || []).map((b) => [b.column, b]));
  const names = [...new Set([...(t.column_names || []), ...maps.keys(), ...bridges.keys()])];
  const rows = names.map((c) => ({
    column: c, maps: maps.get(c) || [], bridge: bridges.get(c) || null,
    sample: (t.samples[c] || []).slice(0, 6).join(", "), described: d.columns[c] || "",
  }));
  const picked = new Set();
  const state = { filter: "pending" };
  const status = el("span", "dash-note");
  const tbody = el("tbody");
  const boxes = [];
  const descInputs = new Map();

  const bulkOps = (action) => {
    const ops = [];
    for (const r of rows) {
      if (!picked.has(r.column)) continue;
      if (action === "confirm") {
        for (const m of r.maps) ops.push({ kind: "mapping", action: "confirm", column: r.column, entity_class: m.entity_class });
        if (r.bridge) ops.push({ kind: "bridge", action: "confirm", column: r.column });
      } else if (action === "clear") {
        if (r.maps.length) ops.push({ kind: "mapping", action: "clear", column: r.column });
        if (r.bridge) ops.push({ kind: "bridge", action: "clear", column: r.column });
      }
    }
    return ops;
  };
  const run = (ops, label, preview) => {
    if (!ops.length) { status.textContent = "Nothing to do for the selected rows."; return; }
    status.textContent = "";
    (preview ? cpWrite : cpApply)(`${base}/batch`, { ...common, ops }, label, cpLoadTables);
  };

  const refresh = () => {
    status.textContent = `${picked.size} selected`;
    for (const b of boxes) b.box.checked = picked.has(b.column);
    master.checked = boxes.length > 0 && boxes.every((b) => !b.tr.hidden ? picked.has(b.column) : true) && picked.size > 0;
  };
  const master = el("input"); master.type = "checkbox"; master.title = "select every row shown";
  master.addEventListener("change", () => {
    for (const b of boxes) if (!b.tr.hidden) { if (master.checked) picked.add(b.column); else picked.delete(b.column); }
    refresh();
  });

  const table = el("table", "dash-table curate-table");
  const head = el("thead"); const hr = el("tr");
  hr.appendChild(cpTd(master));
  for (const h of ["Column", "Proposed class", "Conf.", "Bridge", "Sample values", "Description for text2sql"]) hr.appendChild(el("th", null, h));
  head.appendChild(hr);
  for (const r of rows) {
    const tr = el("tr");
    const box = el("input"); box.type = "checkbox";
    box.addEventListener("change", () => { if (box.checked) picked.add(r.column); else picked.delete(r.column); refresh(); });
    tr.appendChild(cpTd(box));
    tr.appendChild(cpTd(r.column, "curate-col"));
    tr.appendChild(cpTd(r.maps.map((m) => m.entity_class).join(", ") || "—"));
    tr.appendChild(cpTd(r.maps.map((m) => (m.confidence != null ? Number(m.confidence).toFixed(1) : "")).join(", ")));
    tr.appendChild(cpTd(r.bridge ? `proposed${r.bridge.confidence != null ? " " + Number(r.bridge.confidence).toFixed(2) : ""}` : "—"));
    const sampleTd = cpTd(r.sample || "no sample", "curate-sample");
    sampleTd.title = (t.samples[r.column] || []).join(", ");
    tr.appendChild(sampleTd);
    const inp = el("input"); inp.type = "text"; inp.value = r.described; inp.placeholder = "what this column means";
    inp.addEventListener("input", () => { tr.classList.toggle("curate-dirty", inp.value.trim() !== r.described); });
    descInputs.set(r.column, { inp, original: r.described });
    tr.appendChild(cpTd(inp));
    tr.dataset.pending = r.maps.length || r.bridge ? "1" : "";
    tbody.appendChild(tr);
    boxes.push({ column: r.column, box, tr });
  }
  table.append(head, tbody);

  const applyFilter = () => {
    for (const b of boxes) b.tr.hidden = state.filter === "pending" && !b.tr.dataset.pending;
    refresh();
  };
  const filter = el("select");
  for (const [v, l] of [["pending", "columns with a proposal"], ["all", "all columns"]]) { const o = el("option", null, l); o.value = v; filter.appendChild(o); }
  filter.addEventListener("change", () => { state.filter = filter.value; applyFilter(); });

  const bar = el("div", "dash-form curate-bar");
  const sel = (label, pred) => cpBtn(label, () => { for (const b of boxes) if (!b.tr.hidden && pred(b)) picked.add(b.column); refresh(); });
  bar.append(
    el("label", null, "Show "), filter,
    sel("Select all shown", () => true),
    sel("Select high confidence (≥ 0.8)", (b) => rows.find((r) => r.column === b.column).maps.some((m) => (m.confidence || 0) >= 0.8)),
    cpBtn("Clear selection", () => { picked.clear(); refresh(); }),
    cpBtn("Confirm selected", () => run(bulkOps("confirm"), "confirm selected")),
    cpBtn("Preview clearing selected…", () => run(bulkOps("clear"), "clear selected", true)),
    status,
  );

  const classes = el("input"); classes.type = "text"; classes.placeholder = "entity class"; classes.size = 14;
  const setBar = el("div", "dash-form curate-bar");
  setBar.append(
    el("span", "dash-note", "Set a class on the selected columns:"), classes,
    cpBtn("Set class on selected", () => {
      const c = classes.value.trim();
      if (!c) { status.textContent = "Type an entity class first."; return; }
      run(rows.filter((r) => picked.has(r.column)).map((r) => ({ kind: "mapping", action: "set", column: r.column, entity_class: c })), `set ${c}`);
    }),
    cpBtn("Save changed descriptions", () => {
      const ops = [];
      for (const [column, { inp, original }] of descInputs) if (inp.value.trim() !== original) ops.push({ kind: "describe", column, text: inp.value });
      if (!ops.length) { status.textContent = "No description was changed."; return; }
      run(ops, "save descriptions");
    }),
  );

  const tableDesc = el("input"); tableDesc.type = "text"; tableDesc.size = 60; tableDesc.value = d.description || "";
  tableDesc.placeholder = "what this table is: one row per what";
  const tableRow = el("div", "dash-form curate-bar");
  tableRow.append(el("span", "dash-note", "Table description:"), tableDesc,
    cpBtn("Save", () => cpApply(`${base}/describe`, { ...common, column: null, text: tableDesc.value }, `Saved the description of ${t.table}.`, cpLoadTables)));

  const scroll = el("div", "curate-scroll");
  scroll.appendChild(table);
  det.append(bar, scroll, setBar, tableRow);
  if (!t.grain_confirmed) {
    const foot = el("div", "dash-form curate-bar");
    const grain = el("select");
    for (const g of ["instance", "lookup", "normative"]) grain.appendChild(el("option", null, g));
    if (t.grain) grain.value = t.grain;
    foot.append(el("span", "dash-note", "Grain:"), grain, cpBtn("Confirm grain", () => cpApply(`${base}/grain`, { ...common, grain: grain.value }, `Confirmed the grain of ${t.table} as ${grain.value}.`, cpLoadTables)));
    det.appendChild(foot);
  }
  applyFilter();
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
