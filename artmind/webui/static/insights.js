"use strict";

// Insights tab: renders the latest snapshot from /api/insights. Relies on the
// globals el() and api() from dashboard.js. Every piece of snapshot text goes
// through el()/textContent; this file never builds markup from data.

const INS_SVG = "http://www.w3.org/2000/svg";
const INS_LS_KEY = "artmind-insights-domain";
const INS_MAX_NODES = 60;
const INS_SECTION_CAP = 12;

const insDomainEl = document.getElementById("insights-domain");
const insBodyEl = document.getElementById("insights-body");
const insStatusEl = document.getElementById("insights-status");
const insAnchorsEl = document.getElementById("insights-anchors");
const insRefreshBtn = document.getElementById("insights-refresh-btn");
const insForceEl = document.getElementById("insights-force");
let insLoaded = false;
let insPollTimer = null;

// ── small helpers ────────────────────────────────────────────────────
function insLsGet() {
  try { return localStorage.getItem(INS_LS_KEY) || ""; } catch (_e) { return ""; }
}
function insLsSet(value) {
  try { localStorage.setItem(INS_LS_KEY, value); } catch (_e) { /* storage unavailable */ }
}
function svgEl(tag, attrs) {
  const node = document.createElementNS(INS_SVG, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, String(v));
  return node;
}
function idOf(x) {
  return typeof x === "string" ? x : (x && x._id) || "";
}
function fmtVal(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Math.round(v * 1000) / 1000);
  if (typeof v === "string") return v;
  if (Array.isArray(v)) {
    return v.map((x) => (x && typeof x === "object" ? (x.name || x.label || JSON.stringify(x)) : String(x))).join(", ");
  }
  return JSON.stringify(v);
}

// ── force layout (self-contained) ────────────────────────────────────
// Pairwise repulsion + weighted spring attraction on edges + centring, 300
// iterations with linear cooling. O(n^2) per step, so callers cap n at 60.
function forceLayout(nodes, edges, width, height) {
  const n = nodes.length;
  const pos = nodes.map((_, i) => {
    const a = (2 * Math.PI * i) / Math.max(n, 1);
    return { x: width / 2 + Math.cos(a) * width / 4, y: height / 2 + Math.sin(a) * height / 4, dx: 0, dy: 0 };
  });
  const index = new Map(nodes.map((nd, i) => [nd._id, i]));
  const links = [];
  for (const e of edges) {
    const a = index.get(e.source);
    const b = index.get(e.target);
    if (a !== undefined && b !== undefined && a !== b) links.push([a, b, e.weight || 1]);
  }
  const maxW = Math.max(1, ...links.map((l) => l[2]));
  const k = Math.sqrt((width * height) / Math.max(n, 1)) * 0.8;
  const iterations = 300;
  for (let it = 0; it < iterations; it++) {
    const temp = (1 - it / iterations) * (width / 10) + 0.5;
    for (const p of pos) { p.dx = 0; p.dy = 0; }
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let vx = pos[i].x - pos[j].x;
        let vy = pos[i].y - pos[j].y;
        let d = Math.hypot(vx, vy);
        if (d < 0.01) { vx = Math.random() - 0.5; vy = Math.random() - 0.5; d = Math.hypot(vx, vy); }
        const f = (k * k) / d;
        pos[i].dx += (vx / d) * f; pos[i].dy += (vy / d) * f;
        pos[j].dx -= (vx / d) * f; pos[j].dy -= (vy / d) * f;
      }
    }
    for (const [a, b, w] of links) {
      const vx = pos[a].x - pos[b].x;
      const vy = pos[a].y - pos[b].y;
      const d = Math.max(Math.hypot(vx, vy), 0.01);
      const f = ((d * d) / k) * (0.3 + 0.7 * (w / maxW));
      pos[a].dx -= (vx / d) * f; pos[a].dy -= (vy / d) * f;
      pos[b].dx += (vx / d) * f; pos[b].dy += (vy / d) * f;
    }
    for (const p of pos) {
      p.dx += (width / 2 - p.x) * 0.05;   // centring
      p.dy += (height / 2 - p.y) * 0.05;
      const d = Math.max(Math.hypot(p.dx, p.dy), 0.01);
      const step = Math.min(d, temp);
      p.x += (p.dx / d) * step;
      p.y += (p.dy / d) * step;
      p.x = Math.min(width - 20, Math.max(20, p.x));
      p.y = Math.min(height - 20, Math.max(20, p.y));
    }
  }
  return pos;
}

function renderNetwork(card, brokerIds, nPopulation) {
  const wrap = el("div", "ins-net");
  const nodes = (card.nodes || []).slice(0, INS_MAX_NODES);
  const keep = new Set(nodes.map((nd) => nd._id));
  const edges = (card.edges || []).filter((e) => keep.has(e.source) && keep.has(e.target));
  if (!nodes.length) { wrap.appendChild(el("p", "dash-empty", "No members to draw.")); return wrap; }
  const W = 600, H = 400;
  const pos = forceLayout(nodes, edges, W, H);
  const wdeg = nodes.map(() => 0);
  const idx = new Map(nodes.map((nd, i) => [nd._id, i]));
  for (const e of edges) { wdeg[idx.get(e.source)] += e.weight || 1; wdeg[idx.get(e.target)] += e.weight || 1; }
  const maxDeg = Math.max(1, ...wdeg);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, role: "img" });
  for (const e of edges) {
    const a = pos[idx.get(e.source)], b = pos[idx.get(e.target)];
    svg.appendChild(svgEl("line", {
      class: "ins-edge", x1: a.x, y1: a.y, x2: b.x, y2: b.y,
      "stroke-width": 0.5 + 2.5 * ((e.weight || 1) / Math.max(1, ...edges.map((x) => x.weight || 1))),
    }));
  }
  const byWeight = nodes.map((nd, i) => i).sort((a, b) => (nodes[b].weight || 0) - (nodes[a].weight || 0));
  const labelled = new Set(byWeight.slice(0, 12));
  nodes.forEach((nd, i) => {
    const broker = brokerIds.has(nd._id);
    const circle = svgEl("circle", {
      class: "ins-node" + (broker ? " ins-broker" : " ins-alt"),
      cx: pos[i].x, cy: pos[i].y, r: 4 + 9 * (wdeg[i] / maxDeg),
    });
    const title = svgEl("title");
    title.textContent = `${nd.name} (weight ${fmtVal(nd.weight)})${broker ? " — broker" : ""}`;
    circle.appendChild(title);
    svg.appendChild(circle);
    if (labelled.has(i)) {
      const t = svgEl("text", { x: pos[i].x + 8, y: pos[i].y + 3 });
      t.textContent = nd.name;
      svg.appendChild(t);
    }
  });
  wrap.appendChild(svg);
  if (card.truncated || nPopulation > nodes.length) {
    wrap.appendChild(el("p", "dash-note", `showing ${nodes.length} of ${nPopulation || "more"}`));
  }
  return wrap;
}

// ── card bodies ──────────────────────────────────────────────────────
function renderSpark(card) {
  const wrap = el("div", "ins-spark");
  const values = (card.values || []).map(Number);
  const periods = card.periods || [];
  if (!values.length) { wrap.appendChild(el("p", "dash-empty", "No data.")); return wrap; }
  const W = 200, H = 56, pad = 4;
  const lo = Math.min(...values), hi = Math.max(...values);
  const span = hi - lo || 1;
  const xy = values.map((v, i) => [
    pad + (values.length === 1 ? (W - 2 * pad) / 2 : (i * (W - 2 * pad)) / (values.length - 1)),
    H - pad - ((v - lo) / span) * (H - 2 * pad),
  ]);
  const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img" });
  svg.appendChild(svgEl("polyline", { points: xy.map((p) => p.join(",")).join(" ") }));
  xy.forEach(([x, y], i) => {
    const c = svgEl("circle", { cx: x, cy: y, r: 2 });
    const t = svgEl("title");
    t.textContent = `${periods[i] !== undefined ? periods[i] : i}: ${fmtVal(values[i])}`;
    c.appendChild(t);
    svg.appendChild(c);
  });
  wrap.appendChild(svg);
  const axis = el("div", "ins-axis");
  axis.appendChild(el("span", "", `${periods[0] !== undefined ? periods[0] : ""} · ${fmtVal(values[0])}`));
  axis.appendChild(el("span", "", `${periods.length ? periods[periods.length - 1] : ""} · ${fmtVal(values[values.length - 1])}`));
  wrap.appendChild(axis);
  if (card.unit) wrap.appendChild(el("p", "dash-note", card.unit));
  return wrap;
}

function renderStatsList(stats, skip) {
  const dl = el("dl", "ins-stats");
  for (const [k, v] of Object.entries(stats || {})) {
    if (skip.includes(k)) continue;
    dl.appendChild(el("dt", "", k.replace(/_/g, " ")));
    dl.appendChild(el("dd", "", fmtVal(v)));
  }
  return dl;
}

function renderPairs(card) {
  const wrap = el("div");
  const pct = (v) => `${Math.round((v || 0) * 100)}%`;
  for (const [label, value, cls] of [[card.subject || "subject", card.share, ""], ["baseline", card.baseline, "ins-base"]]) {
    wrap.appendChild(el("div", "ins-result", `${label}: ${pct(value)}`));
    const bar = el("div", "ins-bar");
    const fill = el("span", cls);
    fill.style.width = `${Math.min(100, Math.max(0, (value || 0) * 100))}%`;
    bar.appendChild(fill);
    wrap.appendChild(bar);
  }
  wrap.appendChild(el("div", "ins-result", `lift ${fmtVal(card.lift)}`));
  if (card.caption) wrap.appendChild(el("p", "ins-narration", card.caption));
  return wrap;
}

function renderTimeline(card) {
  const ol = el("ol", "ins-timeline");
  for (const ev of card.events || []) ol.appendChild(el("li", "", `${ev.date} · ${ev.kind} · ${ev.label}`));
  return ol;
}

function cardBody(f, ctx) {
  const card = f.card || {};
  switch (card.type) {
    case "series": return renderSpark(card);
    case "pairs": return renderPairs(card);
    case "timeline": return renderTimeline(card);
    case "network": return renderNetwork(card, ctx.brokerIds, (f.stats || {}).size);
    default: return renderStatsList(f.stats, ["headline", "members", "rates", "sample", "triples", "suspects", "examples", "top", "by_property"]);
  }
}

// ── actions ──────────────────────────────────────────────────────────
function askQuestion(f, domain) {
  const subjects = (f.subjects || []).map((s) => `${s.name} (${s._id})`).join(", ");
  return `About the insight '${f.title}' in ${domain}: explain it and check it against the graph (subjects: ${subjects}).`;
}
function promoteText(f, domain) {
  const stats = JSON.stringify(f.stats || {}).slice(0, 1500);
  return `Use artmind-create-view to promote this insight into a view for domain ${domain}. Finding ${f.key} (${f.detector}): ${f.title}. Stats: ${stats}. Set provenance.origin: promoted and source_question: "${f.title}".`;
}
function curationKeys(f, all) {
  if (f.detector === "same_as_candidates") return [f.key];
  const ids = new Set((f.subjects || []).map((s) => s._id));
  if (ids.size < 2) { ids.clear(); for (const x of [(f.stats || {}).canonical, (f.stats || {}).alias]) ids.add(idOf(x)); }
  return all.filter((o) => o.detector === "same_as_candidates"
    && idOf((o.stats || {}).canonical) && ids.has(idOf((o.stats || {}).canonical))
    && ids.has(idOf((o.stats || {}).alias))).map((o) => o.key);
}

function renderActions(f, ctx) {
  const row = el("div", "ins-actions");
  const note = el("span", "ins-result");
  const ask = el("button", "btn-secondary", "Ask in console");
  ask.type = "button";
  ask.addEventListener("click", () => {
    window.open("/?ask=" + encodeURIComponent(askQuestion(f, ctx.domain)), "_blank", "noopener");
  });
  const promote = el("button", "btn-secondary", "Copy promote-to-view prompt");
  promote.type = "button";
  promote.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(promoteText(f, ctx.domain));
      note.textContent = "copied";
    } catch (err) {
      note.textContent = `copy failed: ${err.message}`;
    }
  });
  row.append(ask, promote);
  if (f.detector === "same_as_candidates" || f.detector === "split_signal") {
    const send = el("button", "btn-secondary", "Send to curation");
    send.type = "button";
    send.addEventListener("click", async () => {
      const keys = curationKeys(f, ctx.findings);
      if (!keys.length) { note.textContent = "no matching same_as candidate in this snapshot"; return; }
      if (!window.confirm(`Propose ${keys.length} same-as merge(s) for review in the curation queue? Nothing is merged yet.`)) return;
      send.disabled = true;
      try {
        const res = await api("/api/insights/same-as/propose", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ domain: ctx.domain, keys }),
        });
        note.textContent = `stats ${JSON.stringify(res.stats)}; skipped ${JSON.stringify(res.skipped)}`;
      } catch (err) {
        note.textContent = `failed: ${err.message}`;
      } finally {
        send.disabled = false;
      }
    });
    row.appendChild(send);
  }
  row.appendChild(note);
  return row;
}

function renderCard(f, ctx) {
  const stats = f.stats || {};
  const title = f.card && f.card.type === "network" && stats.circle_name ? `${stats.circle_name}: ${f.title}` : f.title;
  const box = el("article", "ins-card" + (f.card && f.card.type === "network" ? " ins-wide" : ""));
  const head = el("h3", "", title);
  if (f.diff && f.diff !== "same") head.appendChild(el("span", "ins-diff", ` ${f.diff}`));
  box.appendChild(head);
  if (f.narration) {
    const p = el("p", "ins-narration");
    p.appendChild(el("span", "ins-generated", "generated"));
    p.appendChild(document.createTextNode(f.narration));
    box.appendChild(p);
  }
  const bar = el("div", "ins-score");
  bar.title = `score ${fmtVal(f.score)}`;
  const fill = el("span");
  fill.style.width = `${Math.round(Math.min(1, Math.max(0, f.score || 0)) * 100)}%`;
  bar.appendChild(fill);
  box.appendChild(bar);
  box.appendChild(cardBody(f, ctx));
  box.appendChild(renderActions(f, ctx));
  return box;
}

// ── sections ─────────────────────────────────────────────────────────
function renderSection(parent, heading, findings, ctx, hint) {
  const section = el("div", "ins-section");
  section.appendChild(el("h2", "dash-subhead", `${heading} (${findings.length})`));
  if (hint) section.appendChild(el("p", "dash-hint", hint));
  if (!findings.length) {
    section.appendChild(el("p", "dash-empty", "Nothing to show."));
    parent.appendChild(section);
    return;
  }
  const grid = el("div", "ins-grid");
  // Network cards (circles) lead their section: they are the overview the other cards refer to.
  const isNet = (f) => (f.card && f.card.type === "network") ? 1 : 0;
  const sorted = findings.slice().sort((a, b) => (isNet(b) - isNet(a)) || ((b.score || 0) - (a.score || 0)));
  const draw = (items) => { for (const f of items) grid.appendChild(renderCard(f, ctx)); };
  draw(sorted.slice(0, INS_SECTION_CAP));
  section.appendChild(grid);
  if (sorted.length > INS_SECTION_CAP) {
    const more = el("button", "btn-secondary", `Show ${sorted.length - INS_SECTION_CAP} more`);
    more.type = "button";
    more.addEventListener("click", () => { draw(sorted.slice(INS_SECTION_CAP)); more.remove(); });
    section.appendChild(more);
  }
  parent.appendChild(section);
}

function renderHealth(parent, findings, ctx) {
  const groups = new Map();
  for (const f of findings) {
    const key = f.action || "other";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(f);
  }
  const wrap = el("div", "ins-section");
  wrap.appendChild(el("h2", "dash-subhead", `Graph health (${findings.length})`));
  wrap.appendChild(el("p", "dash-hint", "Grouped by the action that would address them."));
  parent.appendChild(wrap);
  for (const [action, items] of groups) renderSection(wrap, `action: ${action}`, items, ctx);
}

function renderSnapshot(snap) {
  const domain = snap.domain;
  const findings = snap.findings || [];
  const brokerIds = new Set(findings.filter((f) => f.detector === "brokers").map((f) => idOf((f.subjects || [])[0])));
  const ctx = { domain, findings, brokerIds };

  const diff = snap.diff || {};
  const llm = snap.llm || {};
  const diffText = ["new", "changed", "gone"].map((k) => `${diff[k] || 0} ${k}`).join(", ");
  const llmText = llm.calls === undefined && !llm.errors ? "captions off"
    : `captions: ${llm.narrated || 0} new, ${llm.cached || 0} cached, ${llm.rejected || 0} rejected` +
      (llm.errors ? `, ${llm.errors} errors` : "") + (llm.model ? ` (${llm.model})` : "");
  insStatusEl.textContent = `as of ${snap.as_of || "?"} · computed ${(snap.created_at || "?").replace("T", " ").replace("Z", " UTC")} · since last run: ${diffText} · ${llmText}`;

  insAnchorsEl.innerHTML = "";
  const anchors = snap.anchors || [];
  if (anchors.length) {
    const box = el("div", "ins-anchor-note");
    box.appendChild(el("strong", "", "Anchors: "));
    box.appendChild(document.createTextNode(anchors.map((a) => `${a.name}${a.rule ? ` (${a.rule})` : ""}`).join(", ")));
    box.appendChild(el("div", "", `wrong? set anchors: in domains/insights/${domain}.yaml`));
    insAnchorsEl.appendChild(box);
  }

  insBodyEl.innerHTML = "";
  const of = (...names) => findings.filter((f) => names.includes(f.detector) && f.kind !== "health");
  renderSection(insBodyEl, "What changed", findings.filter((f) => f.diff === "new" || f.diff === "changed"), ctx);
  renderSection(insBodyEl, "People & circles", of("communities", "brokers", "dormancy"), ctx);
  renderSection(insBodyEl, "Trends", of("salience_trend", "type_trend"), ctx);
  renderSection(insBodyEl, "Associations", of("outcome_lift"), ctx);
  renderSection(insBodyEl, "Transitions", of("transitions"), ctx);
  renderSection(insBodyEl, "Open loops", of("open_loops"), ctx);
  renderHealth(insBodyEl, findings.filter((f) => f.kind === "health"), ctx);

  const plan = snap.plan || [];
  if (plan.length) {
    const details = el("details", "ins-section");
    details.appendChild(el("summary", "", `Skipped or failed detectors (${plan.length})`));
    const ul = el("ul");
    for (const p of plan) ul.appendChild(el("li", "ins-result", fmtVal(p)));
    details.appendChild(ul);
    insBodyEl.appendChild(details);
  }
}

// ── loading ──────────────────────────────────────────────────────────
async function loadInsights() {
  const domain = insDomainEl.value;
  if (!domain) { insBodyEl.innerHTML = ""; insBodyEl.appendChild(el("p", "dash-empty", "No domains.")); return; }
  insLsSet(domain);
  insBodyEl.innerHTML = "";
  insBodyEl.appendChild(el("p", "dash-empty", "Loading…"));
  try {
    const snap = await api(`/api/insights?domain=${encodeURIComponent(domain)}`);
    renderSnapshot(snap);
  } catch (err) {
    insStatusEl.textContent = "";
    insAnchorsEl.innerHTML = "";
    insBodyEl.innerHTML = "";
    insBodyEl.appendChild(el("p", "dash-note", `${err.message} — press Refresh to compute a snapshot.`));
  }
}

async function loadInsightDomains() {
  const domains = await api("/api/domains");
  insDomainEl.innerHTML = "";
  for (const d of domains) {
    const name = typeof d === "string" ? d : d.name;
    const opt = el("option", "", name);
    opt.value = name;
    insDomainEl.appendChild(opt);
  }
  const saved = insLsGet();
  if (saved && [...insDomainEl.options].some((o) => o.value === saved)) insDomainEl.value = saved;
}

function pollRefresh(domain) {
  clearTimeout(insPollTimer);
  insPollTimer = setTimeout(async () => {
    try {
      const p = await api(`/api/insights/refresh/progress?domain=${encodeURIComponent(domain)}`);
      if (p.state === "running") { pollRefresh(domain); return; }
      insRefreshBtn.disabled = false;
      if (p.state === "error") { insStatusEl.textContent = `Refresh failed: ${p.detail || "unknown error"}`; return; }
      await loadInsights();
    } catch (err) {
      insRefreshBtn.disabled = false;
      insStatusEl.textContent = `Refresh status failed: ${err.message}`;
    }
  }, 1500);
}

async function startRefresh() {
  const domain = insDomainEl.value;
  if (!domain) return;
  insRefreshBtn.disabled = true;
  insStatusEl.textContent = "Refreshing…";
  try {
    await api("/api/insights/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, force: insForceEl.checked }),
    });
    pollRefresh(domain);
  } catch (err) {
    insRefreshBtn.disabled = false;
    insStatusEl.textContent = `Refresh failed: ${err.message}`;
  }
}

insRefreshBtn.addEventListener("click", startRefresh);
insDomainEl.addEventListener("change", loadInsights);
document.querySelector('.tab[data-tab="insights"]').addEventListener("click", async () => {
  if (insLoaded) return;
  insLoaded = true;
  try {
    await loadInsightDomains();
    await loadInsights();
  } catch (err) {
    insLoaded = false;
    insBodyEl.innerHTML = "";
    insBodyEl.appendChild(el("p", "dash-note", `Failed to load: ${err.message}`));
  }
});
