"use strict";

// ```artmind-graph blocks in rendered markdown (admin chat, Views tab, Explore -> Graph; chat-ui
// once its endpoint is on). Spec docs/superpowers/specs/2026-11-14-interactive-graph-design.md §3.
//
// marked turns the fence into <pre><code class="language-artmind-graph">. This replaces each such
// block with an interactive Cytoscape canvas drawn from POST /api/graph/run. The data is live, so
// a reloaded conversation redraws. The fence is JSON {"domain", "cypher", "title"?}; a fence that
// does not parse stays a code block and is not run. Cytoscape and the fcose layout are vendored
// and loaded on the first block only (data-graph-libs, in load order). Graph text reaches the
// page only through textContent and Cytoscape labels, never as HTML: conversations hold private
// corpus content.
//   renderArtmindGraph(scope)                -- after a node's markdown is in the DOM; idempotent
//   ArtmindGraph.mount(host, spec, opts)     -- draw one graph into host (the admin drawer uses it)
(function () {
  const LIBS = ((document.currentScript && document.currentScript.dataset.graphLibs) || "").split(/\s+/).filter(Boolean);
  const STRUCTURAL = new Set(["Document", "DocChunk", "Passage", "DocSummary", "Observation"]);
  // 4.5:1 or better against --bg-elev in each theme (checked 2026-11-14).
  const PALETTE = {
    light: ["#2f6db5", "#b8562a", "#2e7d4f", "#8a4fb0", "#b0386a", "#6b6b18", "#1a7479", "#a3461f", "#5a5fc4", "#7a5a2e"],
    dark: ["#6ea8ec", "#f0915e", "#62c48a", "#c08ce6", "#ec7fae", "#c9c45a", "#5cc9d1", "#e8a06a", "#9ba0f2", "#d4ad74"],
  };
  const LAYOUTS = [
    { label: "force", options: { name: "fcose", animate: false, randomize: true, nodeRepulsion: 6000, idealEdgeLength: 90 } },
    { label: "concentric", options: { name: "concentric", animate: false, minNodeSpacing: 18 } },
    { label: "tree", options: { name: "breadthfirst", animate: false, directed: true, spacingFactor: 1.1 } },
  ];
  const MAX_CYPHER = 20000;
  const hosts = new Set();   // hosts with a live cy
  const copies = new Set();  // {cy, canvas} of open full-window copies
  let loading = null;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  }
  const themeName = () => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = () => { s.remove(); reject(new Error("The graph library failed to load.")); };
      document.head.appendChild(s);
    });
  }
  // Only cytoscape is required; a missing layout library falls back to cose.
  function load() {
    if (loading) return loading;
    if (window.cytoscape) return Promise.resolve(window.cytoscape);
    loading = LIBS.reduce(
      (p, src, i) => p.then(() => loadScript(src)).catch((err) => { if (i === 0) throw err; }),
      Promise.resolve(),
    ).then(() => window.cytoscape)
      .catch((err) => { loading = null; throw err; });
    return loading;
  }

  function hashIndex(text, n) {
    let h = 2166136261;
    for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619); }
    return (h >>> 0) % n;
  }
  function classColour(cls) {
    const p = PALETTE[themeName()];
    return p[hashIndex(String(cls || ""), p.length)];
  }
  const nodeSize = (count) => Math.max(18, Math.min(56, 18 + 7 * Math.log2(1 + (Number(count) || 0))));
  function layoutOptions(i) {
    if (i === 0 && !window.cytoscapeFcose) return { name: "cose", animate: false };
    return LAYOUTS[i].options;
  }

  function elementsFrom(data) {
    const els = [];
    for (const n of data.nodes || []) {
      els.push({ group: "nodes", data: {
        id: "n" + n.id,
        label: n.name || n._id || (n.labels || [])[0] || "node",
        colour: classColour(n.class),
        size: nodeSize(n.observation_count),
        structural: (n.labels || []).some((l) => STRUCTURAL.has(l)) ? 1 : 0,
        raw: n,
      } });
    }
    for (const e of data.edges || []) {
      els.push({ group: "edges", data: { id: "e" + e.id, source: "n" + e.source, target: "n" + e.target, label: e.rel_type || e.type || "", raw: e } });
    }
    return els;
  }

  function styleSheet() {
    const text = cssVar("--text", "#2d2a26"), muted = cssVar("--text-muted", "#6f6a60");
    const bg = cssVar("--bg-elev", "#ffffff"), accent = cssVar("--accent", "#6b6fa8");
    return [
      { selector: "node", style: {
        "background-color": "data(colour)", width: "data(size)", height: "data(size)",
        label: "data(label)", color: text, "font-size": 11, "text-valign": "bottom", "text-margin-y": 4,
        "text-wrap": "ellipsis", "text-max-width": 140, "text-outline-color": bg, "text-outline-width": 2,
      } },
      { selector: "node[structural = 1]", style: { shape: "round-rectangle" } },
      { selector: "edge", style: {
        width: 1.4, "line-color": muted, "target-arrow-color": muted, "target-arrow-shape": "triangle",
        "curve-style": "bezier", label: "data(label)", "font-size": 10, color: muted,
        "text-rotation": "autorotate", "text-outline-color": bg, "text-outline-width": 2,
      } },
      { selector: ":selected", style: { "border-width": 3, "border-color": accent, "line-color": accent, "target-arrow-color": accent } },
      { selector: ".faded", style: { opacity: 0.15 } },
    ];
  }

  const sentence = (t) => `${String(t).replace(/[.\s]+$/, "")}.`;
  function failureText(status, body) {
    const detail = body && typeof body.detail === "string" ? body.detail.split("\n")[0] : "";
    if (status === 400) return `Refused: ${sentence(detail || "the query is not allowed")}`;
    if (status === 404) return "This page cannot draw graphs.";
    if (status === 422) {
      const d = body && Array.isArray(body.detail) && body.detail[0] && body.detail[0].msg;
      return d ? `The graph block was rejected: ${sentence(d)}` : "The graph block is missing its domain or cypher.";
    }
    if (status === 429) return detail || "Too many graph runs; try again in a minute.";
    if (status === 504) return `Timed out: ${sentence(detail || "the query took too long")}`;
    return detail ? `Graph could not be drawn: ${sentence(detail)}` : "Graph could not be drawn.";
  }

  async function fetchGraph(spec, extra = {}) {
    let res;
    try {
      res = await fetch("/api/graph/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ domain: spec.domain, cypher: spec.cypher, ...extra }),
      });
    } catch (_e) { throw new Error("The server could not be reached."); }
    let body = null;
    try { body = await res.json(); } catch (_e) { /* not JSON: an error page */ }
    if (!res.ok) throw new Error(failureText(res.status, body));
    if (!body || !Array.isArray(body.nodes) || !Array.isArray(body.edges)) throw new Error("The server sent no graph.");
    return body;
  }

  function summaryText(data) {
    const base = `${data.nodes.length} nodes, ${data.edges.length} edges`;
    if (!data.truncated) return base;
    const parts = [];
    if (data.omitted.nodes || data.omitted.edges) parts.push(`${data.omitted.nodes} nodes and ${data.omitted.edges} edges left out (cap ${data.limits.nodes} nodes)`);
    if (data.rows_capped) parts.push(`only the first ${data.limits.rows} rows read`);
    return `${base}. Capped: ${parts.join("; ")}.`;
  }

  function showInspector(panel, raw, kind, opts, ctx) {
    panel.replaceChildren();
    panel.hidden = false;
    const title = kind === "node" ? (raw.name || raw._id || raw.id) : (raw.rel_type || raw.type);
    panel.appendChild(el("div", "agraph-inspector-title", title));
    const dl = el("dl", "agraph-props");
    const head = kind === "node"
      ? [["class", raw.class], ["valid from", raw._valid_from], ["_id", raw._id]]
      : [["type", raw.type], ["meaning", raw.rel_type]];
    const skip = new Set(["name", "_id", "_valid_from", "entity_class", "rel_type"]);
    const rows = head.concat(Object.entries(raw.properties || {}).filter(([k]) => !skip.has(k)));
    for (const [k, v] of rows) {
      if (v === null || v === undefined || v === "") continue;
      dl.append(el("dt", "", k), el("dd", "", typeof v === "string" ? v : JSON.stringify(v)));
    }
    if (raw.properties_omitted) dl.append(el("dt", "", "…"), el("dd", "", `${raw.properties_omitted} more properties not shown`));
    panel.appendChild(dl);
    if (kind === "node" && ctx.node) {
      const row = el("div", "agraph-actions");
      row.appendChild(window.ArtmindViewer.button("Neighbours", "Add this node's neighbours (or double-click it)", () => expandNode(ctx.cy, ctx, ctx.node)));
      for (const action of opts.inspectorActions || []) {
        row.appendChild(window.ArtmindViewer.button(action.label, action.title, () => action.onClick(raw, ctx)));
      }
      panel.appendChild(row);
    }
  }

  // Server ids are raw; cytoscape ids carry an "n"/"e" prefix (elementsFrom). raw keeps the server id.
  async function expandNode(cy, ctx, node) {
    const cap = window.ArtmindGraph.NODE_CAP;
    if (cap - cy.nodes().length <= 0) { ctx.status.textContent = `At the ${cap}-node cap; nothing added.`; return; }
    ctx.status.textContent = `Expanding ${node.data("label")}…`;
    try {
      const data = await fetchGraph({ domain: ctx.state.spec.domain }, { expand: node.data("raw").id });
      if (cy.destroyed()) return;
      const freshNodes = data.nodes.filter((n) => cy.getElementById("n" + n.id).empty());
      const keptNodes = freshNodes.slice(0, Math.max(0, cap - cy.nodes().length));
      const ids = new Set(cy.nodes().map((n) => n.id()).concat(keptNodes.map((n) => "n" + n.id)));
      const edges = data.edges.filter((e) => ids.has("n" + e.source) && ids.has("n" + e.target) && cy.getElementById("e" + e.id).empty());
      const added = cy.add(elementsFrom({ nodes: keptNodes, edges }));
      added.nodes().position({ ...node.position() });
      cy.layout({ ...layoutOptions(ctx.state.layout), randomize: false }).run();
      const left = freshNodes.length - keptNodes.length;
      ctx.status.textContent = `${keptNodes.length} nodes added around ${node.data("label")}` + (left ? `; ${left} left out (cap ${cap} nodes).` : ".");
    } catch (err) {
      ctx.status.textContent = err.message;
    }
  }

  function wire(cy, inspector, opts, ctx) {
    cy.on("dbltap", "node", (evt) => expandNode(cy, ctx, evt.target));
    cy.on("tap", "node", (evt) => showInspector(inspector, evt.target.data("raw"), "node", opts, { ...ctx, cy, node: evt.target }));
    cy.on("tap", "edge", (evt) => showInspector(inspector, evt.target.data("raw"), "edge", opts, { ...ctx, cy }));
    cy.on("tap", (evt) => { if (evt.target === cy) inspector.hidden = true; });
    cy.on("mouseover", "node", (evt) => cy.elements().not(evt.target.closedNeighborhood()).addClass("faded"));
    cy.on("mouseout", "node", () => cy.elements().removeClass("faded"));
  }

  const slug = (t) => String(t || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 60);
  function downloadPng(cy, title) {
    const a = document.createElement("a");
    a.href = cy.png({ full: true, scale: 2, maxWidth: 4096, maxHeight: 4096, bg: cssVar("--bg", "#ffffff") });
    a.download = `${slug(title) || "graph"}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  function expandGraph(cy, opts, ctx) {
    let big = null;
    const view = window.ArtmindViewer.open({
      label: "Graph",
      buttons: [["fit", "Fit to window", () => big.fit(undefined, 30)]],
      onResize: () => big && big.resize(),
      onClose: () => { copies.delete(entry); big.destroy(); },
    });
    const canvas = el("div", "agraph-full");
    const inspector = el("div", "agraph-inspector");
    inspector.hidden = true;
    const note = el("div", "agraph-note agraph-full-note");
    view.overlay.insertBefore(canvas, view.bar);
    view.overlay.insertBefore(note, view.bar);
    view.overlay.insertBefore(inspector, view.bar);
    big = window.cytoscape({ container: canvas, elements: cy.elements().jsons(), style: styleSheet(), layout: { name: "preset" } });
    const entry = { cy: big, canvas };
    copies.add(entry);
    big.fit(undefined, 30);
    wire(big, inspector, opts, { ...ctx, status: note });
  }

  function toolbar(cy, state, opts, ctx) {
    const V = window.ArtmindViewer;
    const bar = el("div", "agraph-toolbar");
    bar.appendChild(V.button("expand", "Open full window", () => expandGraph(cy, opts, ctx)));
    const layoutBtn = V.button(`layout: ${LAYOUTS[0].label}`, "Switch layout", () => {
      state.layout = (state.layout + 1) % LAYOUTS.length;
      layoutBtn.textContent = `layout: ${LAYOUTS[state.layout].label}`;
      cy.layout(layoutOptions(state.layout)).run();
    });
    bar.appendChild(layoutBtn);
    const copy = V.button("copy Cypher", "Copy the Cypher that drew this graph", async () => {
      try { await navigator.clipboard.writeText(state.cypher); copy.textContent = "copied"; }
      catch (_e) { copy.textContent = "failed"; }
      setTimeout(() => (copy.textContent = "copy Cypher"), 1200);
    });
    bar.appendChild(copy);
    bar.appendChild(V.button("PNG", "Download as PNG", () => downloadPng(cy, state.spec.title)));
    if (opts.onEditCypher) bar.appendChild(V.button("edit Cypher", "Open this Cypher in the graph drawer", () => opts.onEditCypher(state.spec, state.cypher)));
    return bar;
  }

  async function mount(host, spec, opts = {}) {
    destroyHost(host);
    const token = (host._agraphToken = {});
    host.classList.add("agraph");
    host.classList.remove("agraph-failed");
    host.replaceChildren();
    const head = el("div", "agraph-head");
    if (spec.title) head.appendChild(el("div", "agraph-title", spec.title));
    const status = el("div", "agraph-note", "Drawing graph…");
    head.appendChild(status);
    host.appendChild(head);
    let cytoscape, data;
    try {
      [cytoscape, data] = await Promise.all([load(), fetchGraph(spec)]);
    } catch (err) {
      if (host._agraphToken !== token) return null; // a newer draw owns the host
      throw err;
    }
    if (host._agraphToken !== token || !host.isConnected) return null;
    if (!data.nodes.length) {
      const err = new Error("The query returned no nodes.");
      err.cypher = data.cypher;
      throw err;
    }
    status.textContent = summaryText(data);
    const canvas = el("div", "agraph-canvas");
    canvas.title = "Click the graph, then scroll to zoom";
    const inspector = el("div", "agraph-inspector");
    inspector.hidden = true;
    host.append(canvas, inspector);
    const state = { spec, cypher: data.cypher, layout: 0 };
    const cy = cytoscape({ container: canvas, elements: elementsFrom(data), style: styleSheet(), layout: layoutOptions(0), minZoom: 0.05, maxZoom: 6, userZoomingEnabled: false });
    cy.one("tapstart", () => { cy.userZoomingEnabled(true); canvas.removeAttribute("title"); });
    const ctx = { state, status };
    wire(cy, inspector, opts, ctx);
    head.appendChild(toolbar(cy, state, opts, ctx));
    host._agraph = { cy, state, status, opts };
    hosts.add(host);
    if (canvas.clientWidth === 0 && typeof ResizeObserver === "function") {
      const ro = new ResizeObserver(() => {
        if (cy.destroyed()) { ro.disconnect(); return; }
        if (canvas.clientWidth === 0) return;
        ro.disconnect();
        cy.resize();
        cy.layout(layoutOptions(state.layout)).run();
        cy.fit(undefined, 30);
      });
      ro.observe(canvas);
    }
    return host._agraph;
  }

  function destroyHost(host) {
    const g = host._agraph;
    host._agraph = null;
    hosts.delete(host);
    if (g && !g.cy.destroyed()) g.cy.destroy();
  }

  // Free the instances of hosts an ancestor removed (conversation switch, Views re-run).
  function sweep() {
    for (const host of [...hosts]) if (!host.isConnected) destroyHost(host);
    for (const entry of [...copies]) {
      if (!entry.canvas.isConnected) { copies.delete(entry); if (!entry.cy.destroyed()) entry.cy.destroy(); }
    }
  }

  function parseSpec(text) {
    let spec;
    try { spec = JSON.parse(text); } catch (_e) { return { error: "This graph block is not valid JSON, so it was not run." }; }
    if (!spec || typeof spec !== "object" || Array.isArray(spec)) return { error: "A graph block must be a JSON object." };
    const domainOk = (typeof spec.domain === "string" && spec.domain.trim()) || (Array.isArray(spec.domain) && spec.domain.length);
    if (!domainOk || typeof spec.cypher !== "string" || !spec.cypher.trim()) return { error: 'A graph block needs "domain" and "cypher".' };
    if (spec.cypher.length > MAX_CYPHER) return { error: `The graph block's Cypher is longer than ${MAX_CYPHER} characters, so it was not run.` };
    return { spec: { domain: spec.domain, cypher: spec.cypher, title: typeof spec.title === "string" ? spec.title : "" } };
  }

  function fallback(host, message, code) {
    destroyHost(host);
    host.classList.add("agraph-failed");
    host.replaceChildren();
    host.appendChild(el("div", "agraph-note", message));
    const pre = document.createElement("pre");
    pre.appendChild(el("code", "", code));
    host.appendChild(pre);
    if (typeof addCopyButtons === "function") addCopyButtons(host);
  }

  function blockOptions() {
    if (!window.ArtmindGraphDrawer) return {};
    return { onEditCypher: (spec, cypher) => window.ArtmindGraphDrawer.open({ domain: spec.domain, cypher }) };
  }

  window.renderArtmindGraph = function (scope) {
    sweep();
    for (const code of scope.querySelectorAll("pre > code.language-artmind-graph")) {
      const source = code.textContent;
      const host = el("div", "agraph");
      code.parentElement.replaceWith(host);
      const parsed = parseSpec(source);
      if (parsed.error) { fallback(host, parsed.error, source); continue; }
      mount(host, parsed.spec, blockOptions()).catch((err) => {
        if (!host.isConnected) return;
        fallback(host, err.message || "Graph could not be drawn.", err.cypher || source);
      });
    }
  };

  window.ArtmindGraph = { mount, parseSpec, fetchGraph, elementsFrom, layoutOptions, NODE_CAP: 500 };

  function retheme(cy) {
    cy.nodes().forEach((n) => n.data("colour", classColour(n.data("raw").class)));
    cy.style(styleSheet());
  }
  // Re-colour drawn graphs (and open full-window copies) when the page theme is toggled.
  new MutationObserver(() => {
    const all = [...[...hosts].map((h) => h._agraph && h._agraph.cy), ...[...copies].map((c) => c.cy)];
    for (const cy of all) {
      if (!cy || cy.destroyed()) continue;
      try { retheme(cy); } catch (err) { console.error("graph re-theme failed", err); }
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });

  // Conversation switches and Views re-runs remove hosts without telling us.
  let sweepQueued = false;
  new MutationObserver(() => {
    if (sweepQueued || (!hosts.size && !copies.size)) return;
    sweepQueued = true;
    requestAnimationFrame(() => { sweepQueued = false; sweep(); });
  }).observe(document.body, { childList: true, subtree: true });
})();
