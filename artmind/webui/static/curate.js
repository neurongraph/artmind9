"use strict";

// Curate tab (spec 2026-11-12 §2). Relies on el() and api() from dashboard.js.
// Every string from the graph goes through el()/textContent; no markup is built from data.

const curDomainEl = document.getElementById("curate-domain");
const curBodyEl = document.getElementById("curate-sameas-body");
const curPreviewEl = document.getElementById("curate-preview");
const curStatusEl = document.getElementById("curate-status");
const curSelectedEl = document.getElementById("curate-selected");
const curApproveBtn = document.getElementById("curate-approve");
const curRejectBtn = document.getElementById("curate-reject");
let curRows = [];
const curSelected = new Set();
let curLoaded = false;
let curApprovedThisSession = false;

function curPost(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
}
function curQuery() {
  const q = new URLSearchParams({ status: document.getElementById("curate-status-filter").value });
  if (curDomainEl.value) q.set("domain", curDomainEl.value);
  const src = document.getElementById("curate-source-filter").value;
  if (src) q.set("source", src);
  return q;
}
function curUpdateSelection() {
  curSelectedEl.textContent = `${curSelected.size} selected`;
  curApproveBtn.disabled = curRejectBtn.disabled = curSelected.size === 0;
  // One row selected: its canonical can be overridden (any member of the group).
  const wrap = document.getElementById("curate-canonical-wrap");
  const sel = document.getElementById("curate-canonical");
  const row = curSelected.size === 1 ? curRows.find((r) => r.id === [...curSelected][0]) : null;
  wrap.hidden = !row;
  sel.replaceChildren();
  if (row) {
    for (const m of row.members) {
      const opt = el("option", null, m.key);
      opt.value = m.key;
      opt.selected = m.key === row.canonical;
      sel.appendChild(opt);
    }
  }
}
function curClearPreview() { curPreviewEl.replaceChildren(); }

async function curLoadOverview() {
  const strip = document.getElementById("curate-overview");
  const o = await api("/api/curate/overview");
  strip.replaceChildren();
  for (const [label, value] of [["same-as open", o.sameas_open], ["conflicts open", o.conflicts_open],
                                ["projection drift", o.drift === null ? null : (o.drift ? "yes" : "no")]]) {
    const box = el("div", "curate-count");
    box.appendChild(el("b", null, value === null ? "?" : String(value)));
    box.appendChild(el("span", null, label));
    strip.appendChild(box);
  }
  if (o.warnings.length) strip.appendChild(el("p", "dash-note", o.warnings.join("; ")));
}

function curMatchesSearch(row, term) {
  if (!term) return true;
  const hay = [row.reason, row.canonical, ...row.members.map((m) => m.name)].join(" ").toLowerCase();
  return hay.includes(term.toLowerCase());
}

async function curShowEvidence(row, holder) {
  holder.replaceChildren(el("span", "dash-note", "Loading evidence…"));
  try {
    const { members } = await api(`/api/curate/sameas/${encodeURIComponent(row.id)}/evidence`);
    holder.replaceChildren();
    for (const m of members) {
      holder.appendChild(el("strong", null, `${m.name} (${m.class}, ${m.domain})`));
      if (!m.evidence.length) holder.appendChild(el("div", null, "no source passages"));
      for (const ev of m.evidence) holder.appendChild(el("div", null, `[${ev.name || ev.doc_id}] ${ev.text}`));
    }
  } catch (err) { holder.replaceChildren(el("span", "dash-note", `Failed: ${err.message}`)); }
}

function curRenderRows() {
  const term = document.getElementById("curate-search").value.trim();
  const rows = curRows.filter((r) => curMatchesSearch(r, term));
  curBodyEl.replaceChildren();
  if (!rows.length) { curBodyEl.appendChild(el("p", "dash-empty", "No proposals match.")); return; }
  const groups = new Map();
  for (const r of rows) { if (!groups.has(r.class)) groups.set(r.class, []); groups.get(r.class).push(r); }
  for (const [cls, list] of [...groups].sort()) {
    const det = el("details", "curate-group");
    det.open = true;
    det.appendChild(el("summary", null, `${cls || "(no class)"} · ${list.length}`));
    for (const row of list) det.appendChild(curRowNode(row));
    curBodyEl.appendChild(det);
  }
}

function curRowNode(row) {
  const wrap = el("div", "curate-row");
  const box = el("input");
  box.type = "checkbox";
  box.disabled = row.status !== "open";
  box.checked = curSelected.has(row.id);
  box.addEventListener("change", () => {
    box.checked ? curSelected.add(row.id) : curSelected.delete(row.id);
    curUpdateSelection(); curClearPreview();
  });
  wrap.appendChild(box);
  const main = el("div");
  const head = el("div");
  head.appendChild(el("strong", null, row.members.map((m) => m.name).join("  =  ")));
  head.appendChild(el("span", `curate-badge ${row.action}`, row.action === "merge" ? "Merge" : "Link"));
  if (row.status !== "open") head.appendChild(el("span", "curate-badge", row.status));
  main.appendChild(head);
  main.appendChild(el("div", "dash-note", `canonical: ${row.canonical} · ${row.source} · ${row.reason}`));
  const more = el("button", "btn-secondary", "Evidence");
  more.type = "button";
  const holder = el("div", "curate-evidence");
  more.addEventListener("click", () => curShowEvidence(row, holder));
  main.appendChild(more);
  wrap.appendChild(main);
  wrap.appendChild(holder);
  return wrap;
}

async function curLoadSameAs() {
  curStatusEl.textContent = "Loading…";
  curSelected.clear(); curUpdateSelection(); curClearPreview();
  try {
    curRows = (await api(`/api/curate/sameas?${curQuery()}`)).rows;
    curRenderRows();
    curStatusEl.textContent = `${curRows.length} proposal(s)`;
    const domain = curDomainEl.value;
    const ask = `Review the ${curRows.length} same-as proposals${domain ? ` in ${domain}` : ""} and suggest decisions with reasons`;
    document.getElementById("curate-ask").href = `/?ask=${encodeURIComponent(ask)}`;
    await curLoadOverview();
  } catch (err) { curStatusEl.textContent = `Failed to load: ${err.message}`; }
}

// Preview first (dry run), then an explicit Confirm runs the same call for real.
async function curPreviewAndConfirm(path, body, label, onDone) {
  curClearPreview();
  try {
    const dry = await curPost(path, { ...body, dry_run: true });
    const effect = dry.effect || (dry.results && dry.results[0] && dry.results[0].effect) || {};
    curPreviewEl.appendChild(el("p", null, `${label}: ${effect.summary || "ready"}`));
    for (const s of dry.skipped || []) curPreviewEl.appendChild(el("p", "dash-note", `skipped ${s.id}: ${s.reason}`));
    for (const r of dry.results || []) if (r.error) curPreviewEl.appendChild(el("p", "dash-note", `${r.id}: ${r.error}`));
    if (effect.undo) curPreviewEl.appendChild(el("p", "dash-note", `undo: ${effect.undo}`));
    const go = el("button", null, "Confirm");
    go.type = "button";
    go.addEventListener("click", async () => {
      go.disabled = true;
      try {
        const done = await curPost(path, { ...body, dry_run: false });
        await onDone(done);
      } catch (err) { curPreviewEl.appendChild(el("p", "dash-note", `Failed: ${err.message}`)); }
    });
    curPreviewEl.appendChild(go);
  } catch (err) { curPreviewEl.appendChild(el("p", "dash-note", `Preview failed: ${err.message}`)); }
}

curApproveBtn.addEventListener("click", () => {
  const ids = [...curSelected];
  const body = { ids };
  if (ids.length === 1) {
    const row = curRows.find((r) => r.id === ids[0]);
    const pick = document.getElementById("curate-canonical").value;
    if (row && pick && pick !== row.canonical) body.canonical = pick;
  }
  curPreviewAndConfirm("/api/curate/sameas/approve", body, `Approve ${ids.length}`, async (done) => {
    curApprovedThisSession = true;
    curClearPreview();
    const skipped = (done.skipped || []).map((s) => `${s.id}: ${s.reason}`);
    await curLoadSameAs();
    curStatusEl.textContent = `Approved ${done.approved.length}${skipped.length ? `; skipped ${skipped.length}: ${skipped.join("; ")}` : ""}`;
    curOfferSweep();
  });
});

curRejectBtn.addEventListener("click", () => {
  const reason = document.getElementById("curate-reject-reason").value.trim();
  if (!reason) { curStatusEl.textContent = "Give a reason to reject."; return; }
  curPreviewAndConfirm("/api/curate/sameas/reject", { ids: [...curSelected], reason }, `Reject ${curSelected.size}`,
    async () => { curClearPreview(); await curLoadSameAs(); });
});

function curOfferSweep() {
  const holder = document.getElementById("curate-sweep");
  holder.replaceChildren();
  if (!curApprovedThisSession) return;
  holder.appendChild(el("p", "dash-note", "Approvals rebuild only the domains they touch. A full sweep also clears projection drift."));
  const btn = el("button", "btn-secondary", "Preview sweep…");
  btn.type = "button";
  btn.addEventListener("click", async () => {
    try {
      const { plan } = await curPost("/api/curate/sweep", { dry_run: true });
      const go = el("button", null, `Run sweep (${plan.keys} keys)`);
      go.type = "button";
      go.addEventListener("click", async () => { await curPost("/api/curate/sweep", { dry_run: false }); curPollSweep(holder); });
      holder.appendChild(go);
    } catch (err) { holder.appendChild(el("p", "dash-note", `Failed: ${err.message}`)); }
  });
  holder.appendChild(btn);
}

async function curPollSweep(holder) {
  const s = await api("/api/curate/sweep");
  holder.replaceChildren(el("p", "dash-note", s.state === "running" ? "Sweeping…" : s.state === "failed" ? `Sweep failed: ${s.error}` : "Sweep done."));
  if (s.state === "running") setTimeout(() => curPollSweep(holder), 3000);
  else { curApprovedThisSession = false; curLoadOverview(); }
}

async function curLoadDomains() {
  const domains = await api("/api/domains");
  curDomainEl.replaceChildren(el("option", null, ""));
  curDomainEl.firstChild.value = "";
  curDomainEl.firstChild.textContent = "All domains";
  for (const d of domains) curDomainEl.appendChild(el("option", null, d));
}

for (const id of ["curate-status-filter", "curate-source-filter"]) document.getElementById(id).addEventListener("change", curLoadSameAs);
curDomainEl.addEventListener("change", curLoadSameAs);
document.getElementById("curate-reload").addEventListener("click", curLoadSameAs);
document.getElementById("curate-search").addEventListener("input", curRenderRows);
document.querySelector('.tab[data-tab="curate"]').addEventListener("click", async () => {
  if (curLoaded) return;
  curLoaded = true;
  try { await curLoadDomains(); await curLoadSameAs(); }
  catch (err) { curLoaded = false; curStatusEl.textContent = `Failed to load: ${err.message}`; }
});
