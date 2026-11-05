"use strict";

// Views tab: read-only catalogue and runner for saved graph views. Uses el()
// and api() from dashboard.js; marked and DOMPurify are vendored in the page.

const VIEWS_LS_KEY = "artmind-views-domain";
const viewsDomainEl = document.getElementById("views-domain");
const viewsListEl = document.getElementById("views-list");
const viewsDetailEl = document.getElementById("views-detail");
let viewsLoaded = false;

function viewsLsGet() {
  try { return localStorage.getItem(VIEWS_LS_KEY) || ""; } catch (_e) { return ""; }
}
function viewsLsSet(value) {
  try { localStorage.setItem(VIEWS_LS_KEY, value); } catch (_e) { /* storage unavailable */ }
}

function paramEntries(params) {
  if (Array.isArray(params)) return params.map((p) => [p.name, p]);
  return Object.entries(params || {});
}

function makeInput(name, spec) {
  const type = spec.type;
  const def = spec.default;
  let input;
  if (type === "enum") {
    input = el("select");
    for (const v of spec.values || []) { const o = el("option", "", String(v)); o.value = String(v); input.appendChild(o); }
    if (def !== undefined && def !== null) input.value = String(def);
  } else if (type === "bool") {
    input = el("input");
    input.type = "checkbox";
    input.checked = def === true || def === "true";
  } else {
    input = el("input");
    input.type = type === "int" || type === "float" ? "number" : type === "date" ? "date" : "text";
    if (type === "float") input.step = "any";
    if (spec.min !== undefined && spec.min !== null) input.min = String(spec.min);
    if (spec.max !== undefined && spec.max !== null) input.max = String(spec.max);
    if (type === "entity") input.placeholder = "name, or @<_id>";
    else if (type === "list") input.placeholder = "comma-separated";
    if (def !== undefined && def !== null) input.value = Array.isArray(def) ? def.join(",") : String(def);
  }
  input.dataset.param = name;
  return input;
}

function collectParams(form) {
  const out = {};
  for (const input of form.querySelectorAll("[data-param]")) {
    const name = input.dataset.param;
    if (input.type === "checkbox") out[name] = input.checked ? "true" : "false";
    else if (input.value !== "") out[name] = input.value;
  }
  return out;
}

function renderRendered(target, markdown) {
  // Sanitized markdown is the only HTML this tab ever injects.
  target.innerHTML = DOMPurify.sanitize(marked.parse(markdown || ""));
}

async function runView(name, params, resultEl) {
  resultEl.innerHTML = "";
  resultEl.appendChild(el("p", "dash-empty", "Running…"));
  try {
    const res = await api(`/api/views/${encodeURIComponent(name)}/run`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain: viewsDomainEl.value, params }),
    });
    resultEl.innerHTML = "";
    if (res.status === "ok") {
      const body = el("div");
      renderRendered(body, res.rendered);
      resultEl.appendChild(body);
      return;
    }
    resultEl.appendChild(el("p", "dash-note", `status: ${res.status}${res.detail ? ` — ${res.detail}` : ""}`));
    const options = res.candidates || res.suggestions || [];
    const wrap = el("div", "ins-actions");
    for (const c of options) {
      const label = typeof c === "string" ? c : `${c.name || c._id}${c.class ? ` · ${c.class}` : ""}`;
      const id = typeof c === "string" ? null : c._id;
      const btn = el("button", "btn-secondary", label);
      btn.type = "button";
      btn.addEventListener("click", () => {
        const param = res.param || res.parameter || Object.keys(params)[0];
        const next = { ...params };
        if (id && param) next[param] = `@${id}`;
        runView(name, next, resultEl);
      });
      wrap.appendChild(btn);
    }
    resultEl.appendChild(wrap);
  } catch (err) {
    resultEl.innerHTML = "";
    resultEl.appendChild(el("p", "dash-note", `Failed: ${err.message}`));
  }
}

async function showView(name) {
  viewsDetailEl.innerHTML = "";
  viewsDetailEl.appendChild(el("p", "dash-empty", "Loading…"));
  try {
    const data = await api(`/api/views/${encodeURIComponent(name)}`);
    const view = data.view || {};
    viewsDetailEl.innerHTML = "";
    const box = el("section", "ins-card ins-wide");
    box.appendChild(el("h3", "", view.name || name));
    if (view.summary) box.appendChild(el("p", "ins-narration", view.summary));
    const form = el("form", "dash-form");
    form.addEventListener("submit", (e) => e.preventDefault());
    for (const [pname, spec] of paramEntries(view.params)) {
      const label = el("label", "", `${pname}${spec.description ? ` — ${spec.description}` : ""} `);
      label.appendChild(makeInput(pname, spec));
      if (spec.type === "bool") label.className = "checkbox";
      form.appendChild(label);
    }
    const resultEl = el("div", "ins-view-result");
    const run = el("button", "btn-primary", "Run");
    run.type = "submit";
    form.appendChild(run);
    form.addEventListener("submit", () => runView(name, collectParams(form), resultEl));
    box.appendChild(form);
    box.appendChild(resultEl);
    if (data.skill_md) {
      const details = el("details", "ins-guidance");
      details.appendChild(el("summary", "", "Guidance (SKILL.md)"));
      details.appendChild(el("pre", "", data.skill_md));
      box.appendChild(details);
    }
    viewsDetailEl.appendChild(box);
  } catch (err) {
    viewsDetailEl.innerHTML = "";
    viewsDetailEl.appendChild(el("p", "dash-note", `Failed: ${err.message}`));
  }
}

async function loadViews() {
  const domain = viewsDomainEl.value;
  viewsLsSet(domain);
  viewsListEl.innerHTML = "";
  viewsDetailEl.innerHTML = "";
  try {
    const data = await api(`/api/views?domain=${encodeURIComponent(domain)}`);
    const views = data.views || [];
    if (!views.length) viewsListEl.appendChild(el("p", "dash-empty", "No saved views for this domain."));
    for (const v of views) {
      const item = el("article", "ins-card ins-view-item");
      item.tabIndex = 0;
      item.appendChild(el("h3", "", v.name));
      if (v.summary) item.appendChild(el("p", "ins-narration", v.summary));
      for (const ex of v.examples || []) item.appendChild(el("p", "ins-result", `e.g. ${ex}`));
      item.addEventListener("click", () => showView(v.name));
      item.addEventListener("keydown", (e) => { if (e.key === "Enter") showView(v.name); });
      viewsListEl.appendChild(item);
    }
    if ((data.invalid || []).length) {
      viewsListEl.appendChild(el("p", "dash-note", `${data.invalid.length} invalid view(s) not shown.`));
    }
  } catch (err) {
    viewsListEl.appendChild(el("p", "dash-note", `Failed to load views: ${err.message}`));
  }
}

viewsDomainEl.addEventListener("change", loadViews);
document.querySelector('.tab[data-tab="views"]').addEventListener("click", async () => {
  if (viewsLoaded) return;
  viewsLoaded = true;
  try {
    const domains = await api("/api/domains");
    viewsDomainEl.innerHTML = "";
    for (const d of domains) {
      const name = typeof d === "string" ? d : d.name;
      const opt = el("option", "", name);
      opt.value = name;
      viewsDomainEl.appendChild(opt);
    }
    const saved = viewsLsGet();
    if (saved && [...viewsDomainEl.options].some((o) => o.value === saved)) viewsDomainEl.value = saved;
    await loadViews();
  } catch (err) {
    viewsLoaded = false;
    viewsListEl.appendChild(el("p", "dash-note", `Failed to load: ${err.message}`));
  }
});
