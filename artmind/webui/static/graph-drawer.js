"use strict";

// Admin console graph drawer (spec 2026-11-14 §4): domain -> question -> Generate (the existing
// /api/explore/cypher) -> editable Cypher -> Run (/api/graph/run through ArtmindGraph.mount) ->
// canvas -> inspector. It stays open while the conversation goes on. "Ask about this" puts a
// question in the chat input and never sends it. Text goes through textContent only.
// window.ArtmindGraphDrawer.open({domain, cypher}) is what a chat block's "edit Cypher" calls.
(function () {
  const drawer = document.getElementById("graph-drawer");
  if (!drawer) return;
  const LS_KEY = "artmind-graph-domain"; // shared with Explore -> Graph
  const $ = (id) => document.getElementById(id);
  const domainEl = $("gd-domain"), questionEl = $("gd-question"), cypherEl = $("gd-cypher");
  const statusEl = $("gd-status"), canvasEl = $("gd-canvas");
  const generateEl = $("gd-generate"), runEl = $("gd-run"), browserEl = $("gd-browser");
  const toggleEl = $("graph-drawer-toggle"), closeEl = $("graph-drawer-close");
  let domainsLoaded = null;
  drawer.inert = true;

  async function call(path, body) {
    const init = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    const res = await fetch(path, init);
    let data = null;
    try { data = await res.json(); } catch (_e) { /* not JSON */ }
    if (!res.ok) throw new Error((data && typeof data.detail === "string" && data.detail) || `HTTP ${res.status}`);
    return data;
  }
  const lsGet = () => { try { return localStorage.getItem(LS_KEY) || ""; } catch (_e) { return ""; } };
  const lsSet = (v) => { try { localStorage.setItem(LS_KEY, v); } catch (_e) { /* storage unavailable */ } };
  const say = (text) => { statusEl.textContent = text; };

  function loadDomains() {
    if (!domainsLoaded) {
      domainsLoaded = call("/api/explore/domains").then((domains) => {
        domainEl.replaceChildren();
        for (const d of domains) {
          const opt = document.createElement("option");
          opt.value = d.name;
          opt.textContent = d.entities ? `${d.name} (${d.entities} entities)` : `${d.name} (empty)`;
          domainEl.appendChild(opt);
        }
        const saved = domains.find((d) => d.name === lsGet());
        if (saved && saved.entities) domainEl.value = saved.name;
      }).catch((err) => { domainsLoaded = null; throw err; });
    }
    return domainsLoaded;
  }

  function closeOthers() {
    const trace = document.getElementById("drawer-close");
    if (document.body.classList.contains("drawer-open") && trace) trace.click();
    const help = document.getElementById("help-drawer");
    if (help && help.classList.contains("open")) {
      help.classList.remove("open");
      help.setAttribute("aria-hidden", "true");
      help.inert = true;
      const hc = document.getElementById("help-drawer-close");
      if (hc) hc.setAttribute("tabindex", "-1");
    }
  }

  function setOpen(open) {
    if (open) closeOthers();
    drawer.classList.toggle("open", open);
    drawer.setAttribute("aria-hidden", String(!open));
    drawer.inert = !open;
    toggleEl.setAttribute("aria-expanded", String(open));
    if (open) closeEl.removeAttribute("tabindex"); else closeEl.setAttribute("tabindex", "-1");
    document.body.classList.toggle("graph-open", open);
    if (open) {
      questionEl.focus();
      loadDomains().catch((err) => say(`Failed to load domains: ${err.message}`));
    }
  }
  function closeFromElsewhere() { if (drawer.classList.contains("open")) setOpen(false); }

  function fillPrompt(text) {
    const prompt = document.getElementById("prompt");
    if (!prompt) return;
    prompt.value = text;
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
    prompt.focus();
  }
  const askAction = {
    label: "Ask about this",
    title: "Put a question about this node in the chat box (it is not sent)",
    onClick: (raw) => fillPrompt(`tell me about ${raw.name || raw._id || "this"}`),
  };

  async function generate() {
    const question = questionEl.value.trim();
    if (!question || !domainEl.value) { say("Pick a domain and type a question."); return; }
    generateEl.disabled = true;
    say("Generating Cypher…");
    try {
      const data = await call("/api/explore/cypher", { question, domains: [domainEl.value] });
      cypherEl.value = data.run_cypher;
      const warnings = (data.warnings || []).join(" ");
      say(warnings ? `Warning: ${warnings} Review the Cypher, then Run.` : "Review the Cypher, then Run.");
    } catch (err) {
      say(`Could not generate Cypher: ${err.message}`);
    } finally {
      generateEl.disabled = false;
    }
  }

  async function run() {
    const cypher = cypherEl.value.trim();
    if (!cypher || !domainEl.value) { say("Pick a domain and write or generate Cypher."); return; }
    lsSet(domainEl.value);
    runEl.disabled = true;
    say("");
    try {
      await window.ArtmindGraph.mount(canvasEl, { domain: domainEl.value, cypher }, { inspectorActions: [askAction] });
    } catch (err) {
      canvasEl.replaceChildren();
      say(err.message || "Graph could not be drawn.");
    } finally {
      runEl.disabled = false;
    }
  }

  async function openBrowser() {
    try {
      const data = await call("/api/explore/cypher/link", { cypher: cypherEl.value, domains: [domainEl.value] });
      if (!data.browser_url) { say("No Neo4j Browser configured for this vault (ARTMIND_KG_NEO4J_BROWSER_URL)."); return; }
      // noopener: Neo4j Browser must not get a handle on this admin page.
      window.open(data.browser_url, "_blank", "noopener,noreferrer");
      say("Opened Neo4j Browser. Press Run there; log in if it asks.");
    } catch (err) {
      say(`Not opened: ${err.message}`);
    }
  }

  async function open(spec = {}) {
    if (spec.cypher) cypherEl.value = spec.cypher;
    setOpen(true);
    try { await loadDomains(); } catch (_e) { return; }
    const name = Array.isArray(spec.domain) ? (spec.domain.length === 1 ? spec.domain[0] : null) : spec.domain;
    if (!spec.cypher) { if (name && [...domainEl.options].some((o) => o.value === name)) domainEl.value = name; return; }
    if (!name || ![...domainEl.options].some((o) => o.value === name)) {
      const shown = Array.isArray(spec.domain) ? spec.domain.join(", ") : spec.domain;
      say(`This block's domain ${shown} is not in the list; pick one and press Run.`);
      return;
    }
    domainEl.value = name;
    await run();
  }

  toggleEl.addEventListener("click", () => setOpen(!drawer.classList.contains("open")));
  closeEl.addEventListener("click", () => { setOpen(false); toggleEl.focus(); });
  // The trace and help drawers share this edge: opening either closes the graph drawer.
  for (const id of ["trace-toggle", "help-toggle"]) {
    const t = document.getElementById(id);
    if (t) t.addEventListener("click", closeFromElsewhere, true);
  }
  generateEl.addEventListener("click", generate);
  runEl.addEventListener("click", run);
  browserEl.addEventListener("click", openBrowser);
  domainEl.addEventListener("change", () => lsSet(domainEl.value));

  window.ArtmindGraphDrawer = { open };
})();
