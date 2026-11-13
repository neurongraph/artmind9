"use strict";

// Explore -> Graph tab: question -> read-only Cypher -> Neo4j Browser.
// Uses el() and api() from dashboard.js. Nothing runs here: the operator edits
// the Cypher, then opens Neo4j Browser (which pre-fills its editor) and runs it.
// `/dashboard?tab=graph&q=<question>` (the admin chat's "View as graph" button)
// pre-fills the question.

const GRAPH_LS_KEY = "artmind-graph-domain";
const graphDomainEl = document.getElementById("graph-domain");
const graphQuestionEl = document.getElementById("graph-question");
const graphGenerateEl = document.getElementById("graph-generate-btn");
const graphStatusEl = document.getElementById("graph-status");
const graphResultEl = document.getElementById("graph-result");
const graphCypherEl = document.getElementById("graph-cypher");
const graphOpenEl = document.getElementById("graph-open-btn");
const graphCopyEl = document.getElementById("graph-copy-btn");
let graphLoaded = false;

function graphLsGet() {
  try { return localStorage.getItem(GRAPH_LS_KEY) || ""; } catch (_e) { return ""; }
}
function graphLsSet(value) {
  try { localStorage.setItem(GRAPH_LS_KEY, value); } catch (_e) { /* storage unavailable */ }
}

function graphSetStatus(text) {
  graphStatusEl.textContent = text;
}

function openGraphBrowser(url) {
  // noopener: Neo4j Browser must not get a handle on this admin page.
  window.open(url, "_blank", "noopener,noreferrer");
}

async function graphGenerate() {
  const question = graphQuestionEl.value.trim();
  const domain = graphDomainEl.value;
  if (!question || !domain) {
    graphSetStatus("Pick a domain and type a question.");
    return;
  }
  graphGenerateEl.disabled = true;
  graphResultEl.hidden = true;
  graphSetStatus("Generating Cypher…");
  try {
    const data = await api("/api/explore/cypher", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, domains: [domain] }),
    });
    graphCypherEl.value = data.standalone_cypher;
    graphResultEl.hidden = false;
    graphOpenEl.dataset.url = data.browser_url || "";
    graphOpenEl.disabled = false;
    const note = data.browser_url
      ? "Review or edit the Cypher, then open it in Neo4j Browser and press Run."
      : "No Neo4j Browser configured for this vault: set ARTMIND_KG_NEO4J_BROWSER_URL in its .artmind/config.env. You can still copy the Cypher.";
    const warnings = (data.warnings || []).join(" ");
    graphSetStatus(warnings ? `Warning: ${warnings} ${note}` : note);
  } catch (err) {
    graphSetStatus(`Could not generate Cypher: ${err.message}`);
  } finally {
    graphGenerateEl.disabled = false;
  }
}

async function graphOpen() {
  // The text box is editable, so ask the server for a link to what is there now
  // (it re-checks the edited query is still read-only).
  graphOpenEl.disabled = true;
  try {
    const data = await api("/api/explore/cypher/link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cypher: graphCypherEl.value }),
    });
    if (!data.browser_url) {
      graphSetStatus("No Neo4j Browser configured for this vault (ARTMIND_KG_NEO4J_BROWSER_URL).");
      return;
    }
    openGraphBrowser(data.browser_url);
    graphSetStatus("Opened Neo4j Browser. Press Run there; log in once if it asks.");
  } catch (err) {
    graphSetStatus(`Not opened: ${err.message}`);
  } finally {
    graphOpenEl.disabled = false;
  }
}

async function graphCopy() {
  try {
    await navigator.clipboard.writeText(graphCypherEl.value);
    graphCopyEl.textContent = "Copied";
  } catch (_err) {
    graphCopyEl.textContent = "Copy failed";
  }
  setTimeout(() => { graphCopyEl.textContent = "Copy Cypher"; }, 1200);
}

graphGenerateEl.addEventListener("click", graphGenerate);
graphOpenEl.addEventListener("click", graphOpen);
graphCopyEl.addEventListener("click", graphCopy);
graphDomainEl.addEventListener("change", () => graphLsSet(graphDomainEl.value));

// Prefill from the admin chat's "View as graph" link.
{
  const q = new URLSearchParams(window.location.search).get("q");
  if (q) graphQuestionEl.value = q;
}

document.querySelector('.tab[data-tab="graph"]').addEventListener("click", async () => {
  if (graphLoaded) return;
  graphLoaded = true;
  try {
    // Most populated first; an empty domain is labelled so it is not picked by mistake.
    const domains = await api("/api/explore/domains");
    graphDomainEl.innerHTML = "";
    for (const d of domains) {
      const label = d.entities ? `${d.name} (${d.entities} entities)` : `${d.name} (empty)`;
      const opt = el("option", "", label);
      opt.value = d.name;
      graphDomainEl.appendChild(opt);
    }
    // A remembered choice only wins while it still has data.
    const saved = graphLsGet();
    const savedDomain = domains.find((d) => d.name === saved);
    if (savedDomain && savedDomain.entities) graphDomainEl.value = saved;
  } catch (err) {
    graphLoaded = false;
    graphSetStatus(`Failed to load domains: ${err.message}`);
  }
});
