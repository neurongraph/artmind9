"use strict";

// ── theme ────────────────────────────────────────────────────────────
document.getElementById("theme-toggle").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next;
  localStorage.setItem("artmind-theme", next);
});

// ── helpers ──────────────────────────────────────────────────────────
function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function fmtTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString();
}

async function api(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `${response.status} ${response.statusText}`);
  }
  return response.status === 204 ? null : response.json();
}

// ── tabs ─────────────────────────────────────────────────────────────
function initTabs() {
  for (const bar of document.querySelectorAll(".tabs[data-tab-group]")) {
    const group = bar.dataset.tabGroup;
    bar.addEventListener("click", (event) => {
      const btn = event.target.closest(".tab[data-tab]");
      if (!btn || !bar.contains(btn)) return;
      for (const other of bar.querySelectorAll(".tab")) {
        other.classList.toggle("active", other === btn);
      }
      const selector = `.tab-panel[data-tab-group="${group}"]`;
      for (const panel of document.querySelectorAll(selector)) {
        panel.classList.toggle("active", panel.dataset.tabPanel === btn.dataset.tab);
      }
    });
  }
}

// ── domain picker ────────────────────────────────────────────────────
const ingestDomainEl = document.getElementById("ingest-domain");
const embedDomainEl = document.getElementById("embed-domain");
const artifactsDomainEl = document.getElementById("artifacts-domain");
const pullDomainEl = document.getElementById("pull-domain");
const importDomainEl = document.getElementById("import-domain");

async function loadDomains() {
  const domains = await api("/api/domains");
  for (const select of [ingestDomainEl, embedDomainEl, artifactsDomainEl, pullDomainEl, importDomainEl]) {
    select.innerHTML = "";
    for (const domain of domains) {
      select.appendChild(el("option", null, domain));
    }
  }
  if (domains.length) {
    refreshStats(domains);
    refreshArtifacts();
  }
}

const structuredIngestDomainEl = document.getElementById("structured-ingest-domain");
const structuredTablesDomainEl = document.getElementById("structured-tables-domain");

async function loadStructuredDomains() {
  const domains = await api("/api/domains");
  structuredIngestDomainEl.innerHTML = "";
  for (const domain of domains) structuredIngestDomainEl.appendChild(el("option", null, domain));

  structuredTablesDomainEl.innerHTML = "";
  const allOpt = el("option", null, "All domains");
  allOpt.value = "";
  structuredTablesDomainEl.appendChild(allOpt);
  for (const domain of domains) structuredTablesDomainEl.appendChild(el("option", null, domain));

  refreshStructuredTables();
}

structuredTablesDomainEl.addEventListener("change", refreshStructuredTables);

// ── stats ────────────────────────────────────────────────────────────
async function refreshStats(domains) {
  const statCardsEl = document.getElementById("stat-cards");
  statCardsEl.innerHTML = "";
  for (const domain of domains) {
    try {
      const stats = await api(`/api/stats?domain=${encodeURIComponent(domain)}`);
      const card = el("div", "stat-card");
      card.appendChild(el("div", "stat-card-title", domain));
      const rows = el("div", "stat-card-rows");
      for (const entry of stats.rows || []) {
        if (!entry.label) continue;
        const row = el("div", "stat-card-row");
        row.appendChild(el("span", "stat-key", entry.label));
        row.appendChild(el("span", "stat-value", String(entry.count)));
        rows.appendChild(row);
      }
      card.appendChild(rows);
      statCardsEl.appendChild(card);
    } catch (err) {
      // stats are best-effort; skip a domain that fails rather than blocking the page
    }
  }
}

// ── ingest form ──────────────────────────────────────────────────────
document.getElementById("ingest-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const domain = ingestDomainEl.value;
  const path = document.getElementById("ingest-path").value.trim();
  if (!domain || !path) return;
  try {
    const stageOnly = document.getElementById("ingest-stage-only").checked;
    await api("/api/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, path, stageOnly }),
    });
    document.getElementById("ingest-path").value = "";
    refreshActiveJobs();
  } catch (err) {
    alert(`Ingest failed: ${err.message}`);
  }
});

document.getElementById("embed-btn").addEventListener("click", async () => {
  const resultEl = document.getElementById("embed-result");
  resultEl.textContent = "Running…";
  try {
    const result = await api("/api/embed-entities", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain: embedDomainEl.value }),
    });
    resultEl.textContent = JSON.stringify(result);
  } catch (err) {
    resultEl.textContent = `Failed: ${err.message}`;
  }
});

// ── active jobs ──────────────────────────────────────────────────────
// Keys of expanded file rows, so a 2s poll rebuild doesn't collapse them.
const expandedFiles = new Set();

function fileStatusDot(status) {
  const cls = status === "completed" ? "done"
            : status === "failed" ? "error"
            : status === "processing" ? "running" : "";
  return el("span", `dot ${cls}`.trim());
}

async function refreshActiveJobs() {
  const container = document.getElementById("active-jobs");
  const jobs = await api("/api/jobs/active");

  const countEl = document.getElementById("active-tab-count");
  if (countEl) countEl.textContent = jobs.length ? `· ${jobs.length}` : "";

  if (!jobs.length) {
    container.innerHTML = '<p class="dash-empty">No active or queued jobs.</p>';
    return;
  }
  container.innerHTML = "";

  for (const job of jobs) {
    const card = el("div", "job-card");
    const pct = job.fileCount ? Math.round((100 * job.processedCount) / job.fileCount) : 0;
    card.appendChild(el("div", "job-card-head",
      `${job.jobId.slice(0, 10)}… · ${job.domain} · ${job.status.toUpperCase()}`));
    const bar = el("div", "progress-bar");
    const fill = el("div", "progress-fill");
    fill.style.width = `${pct}%`;
    bar.appendChild(fill);
    card.appendChild(bar);
    card.appendChild(el("div", "dash-note", `${job.processedCount}/${job.fileCount} files`));

    const fileRows = el("div", "file-rows");
    for (const f of job.files) {
      const docName = f.filename.split("/").pop();
      const key = `${job.jobId}::${docName}`;
      const wasExpanded = expandedFiles.has(key);

      const row = document.createElement("details");
      row.className = "file-row";

      const summary = document.createElement("summary");
      summary.appendChild(fileStatusDot(f.status));
      summary.appendChild(el("span", "file-name", docName));
      summary.appendChild(el("span", "dash-note", f.currentStep || f.status));
      row.appendChild(summary);

      const drill = el("div", "chunk-drill");
      row.appendChild(drill);

      row.addEventListener("toggle", () => {
        if (row.open) {
          expandedFiles.add(key);
          // quiet only when this row was already expanded going into this poll —
          // a genuine first click has nothing on screen yet, so it should show "Loading chunks…"
          showChunkGrid(drill, job.jobId, job.domain, docName, wasExpanded);
        } else {
          expandedFiles.delete(key);
        }
      });

      // Restore prior expansion; setting .open fires the toggle handler above.
      if (wasExpanded) row.open = true;

      fileRows.appendChild(row);
    }
    card.appendChild(fileRows);
    container.appendChild(card);
  }
}

// ── completed jobs + detail drawer ───────────────────────────────────
const jobDrawerEl = document.getElementById("job-drawer");
const jobDrawerCloseEl = document.getElementById("job-drawer-close");
jobDrawerEl.inert = true;

function openJobDrawer() {
  document.body.classList.add("drawer-open");
  jobDrawerEl.setAttribute("aria-hidden", "false");
  jobDrawerEl.inert = false;
  jobDrawerCloseEl.removeAttribute("tabindex");
}
function closeJobDrawer() {
  document.body.classList.remove("drawer-open");
  jobDrawerEl.setAttribute("aria-hidden", "true");
  jobDrawerEl.inert = true;
  jobDrawerCloseEl.setAttribute("tabindex", "-1");
}
jobDrawerCloseEl.addEventListener("click", closeJobDrawer);

function pip(status) {
  const cls = status === "ok" ? "done" : status === "failed" ? "error" : "";
  return el("span", `dot ${cls}`.trim());
}

async function showChunkGrid(container, jobId, domain, docName, quiet = false) {
  if (!quiet) container.innerHTML = "Loading chunks…";
  try {
    const chunks = await api(`/api/jobs/${encodeURIComponent(jobId)}/chunks?doc=${encodeURIComponent(docName)}`);
    container.innerHTML = "";
    if (!chunks.length) {
      container.appendChild(el("div", "dash-note", "No chunk status recorded."));
      return;
    }
    const grid = el("div", "chunk-grid");
    for (const c of chunks) {
      const row = el("div", "chunk-row");
      row.appendChild(el("span", "chunk-seq", `#${c.seq}`));
      row.appendChild(pip(c.e));
      row.appendChild(pip(c.p));
      row.appendChild(pip(c.r));
      grid.appendChild(row);
    }
    container.appendChild(grid);
    container.appendChild(el("div", "dash-note", "entities · properties · relationships"));

    const resumeBtn = el("button", "btn-link", "Resume extraction");
    resumeBtn.addEventListener("click", async () => {
      resumeBtn.disabled = true;
      resumeBtn.textContent = "Resuming…";
      try {
        await api(`/api/documents/${encodeURIComponent(docName)}/resume-extract`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ domain }),
        });
        await showChunkGrid(container, jobId, domain, docName);
      } catch (err) {
        alert(`Resume extraction failed: ${err.message}`);
        resumeBtn.disabled = false;
        resumeBtn.textContent = "Resume extraction";
      }
    });
    container.appendChild(resumeBtn);
  } catch (err) {
    container.innerHTML = "";
    container.appendChild(el("div", "dash-note", `Failed to load chunks: ${err.message}`));
  }
}

async function showJobDetail(jobId, domain) {
  const body = document.getElementById("job-drawer-body");
  body.innerHTML = "Loading…";
  openJobDrawer();
  try {
    const result = await api(`/api/jobs/${encodeURIComponent(jobId)}/results`);
    body.innerHTML = "";
    if (result.errorMessage) body.appendChild(el("div", "dash-note", `Error: ${result.errorMessage}`));
    for (const f of result.files) {
      const docName = f.filename.split("/").pop();
      const row = el("div", "tool-card");
      row.appendChild(el("div", "tool-head", `${f.status}: ${docName}`));
      if (f.errorMessage) row.appendChild(el("div", "dash-note", f.errorMessage));

      const chunksBtn = el("button", "btn-link", "Show chunks");
      const chunksContainer = el("div", "chunk-container");
      chunksBtn.addEventListener("click", () => showChunkGrid(chunksContainer, jobId, domain, docName));
      row.appendChild(chunksBtn);
      row.appendChild(chunksContainer);
      body.appendChild(row);
    }
  } catch (err) {
    body.textContent = `Failed to load: ${err.message}`;
  }
}

async function retryJob(jobId, includeSkipped) {
  try {
    await api(`/api/jobs/${encodeURIComponent(jobId)}/retry`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ includeSkipped }),
    });
    refreshActiveJobs();
    refreshCompletedJobs();
  } catch (err) {
    alert(`Retry failed: ${err.message}`);
  }
}

async function refreshCompletedJobs() {
  const tbody = document.querySelector("#completed-table tbody");
  const jobs = await api("/api/jobs/completed");
  tbody.innerHTML = "";
  for (const job of jobs) {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", null, `${job.jobId.slice(0, 10)}…`));
    tr.appendChild(el("td", null, job.domain));
    tr.appendChild(el("td", null, `${job.processedCount}/${job.fileCount}`));
    tr.appendChild(el("td", `status-${job.status}`, job.status));
    tr.appendChild(el("td", null, fmtTime(job.completedAt)));

    const actions = el("td");
    const viewBtn = el("button", "btn-link", "View");
    viewBtn.addEventListener("click", () => showJobDetail(job.jobId, job.domain));
    actions.appendChild(viewBtn);
    if (job.status === "failed") {
      const retryBtn = el("button", "btn-link", "Retry");
      retryBtn.addEventListener("click", () => retryJob(job.jobId, false));
      actions.appendChild(retryBtn);
    }
    tr.appendChild(actions);
    tbody.appendChild(tr);
  }
}

// ── knowledge artifacts panel ────────────────────────────────────────
async function refreshArtifacts() {
  const container = document.getElementById("artifact-cards");
  const domain = artifactsDomainEl.value;
  if (!domain) return;
  const artifacts = await api(`/api/artifacts?domain=${encodeURIComponent(domain)}`);
  if (!artifacts.length) {
    container.innerHTML = '<p class="dash-empty">No documents extracted yet for this domain.</p>';
    return;
  }
  container.innerHTML = "";
  for (const a of artifacts) {
    const card = el("div", "job-card");
    const head = el("div", "job-card-head", a.name);
    head.appendChild(el("span", a.inGraph ? "state-badge in-graph" : "state-badge staged",
                        a.inGraph ? "in graph" : "staged"));
    card.appendChild(head);
    card.appendChild(el("div", "dash-note",
      `${a.entityCount} entities · ${a.propertyCount} properties · ${a.relationshipCount} relationships`));

    if (!a.inGraph) {
      const commitBtn = el("button", "btn-link", "Write to graph");
      commitBtn.addEventListener("click", async () => {
        commitBtn.disabled = true;
        commitBtn.textContent = "Writing…";
        try {
          await api(`/api/artifacts/${encodeURIComponent(domain)}/${encodeURIComponent(a.doc)}/write-to-graph`,
                    { method: "POST" });
          await refreshArtifacts();
        } catch (err) {
          alert(`Write to graph failed: ${err.message}`);
          commitBtn.disabled = false;
          commitBtn.textContent = "Write to graph";
        }
      });
      card.appendChild(commitBtn);
    }

    const exportLink = el("a", "btn-link", "Export bundle");
    exportLink.href = `/api/artifacts/${encodeURIComponent(domain)}/${encodeURIComponent(a.doc)}/bundle`;
    card.appendChild(exportLink);
    container.appendChild(card);
  }
}

artifactsDomainEl.addEventListener("change", refreshArtifacts);

document.getElementById("pull-kg-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const resultEl = document.getElementById("pull-result");
  resultEl.textContent = "Pulling…";
  try {
    const result = await api("/api/artifacts/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        repo: document.getElementById("pull-repo").value.trim(),
        repoPath: document.getElementById("pull-repo-path").value.trim(),
        domain: pullDomainEl.value,
      }),
    });
    resultEl.textContent = `Pulled ${result.pulledCount} document(s).`;
    if (pullDomainEl.value === artifactsDomainEl.value) refreshArtifacts();
  } catch (err) {
    resultEl.textContent = `Failed: ${err.message}`;
  }
});

document.getElementById("import-kg-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const resultEl = document.getElementById("import-result");
  const fileInput = document.getElementById("import-file");
  const file = fileInput.files[0];
  if (!file) return;
  resultEl.textContent = "Importing…";
  const formData = new FormData();
  formData.append("domain", importDomainEl.value);
  formData.append("doc", document.getElementById("import-doc").value.trim());
  formData.append("file", file);
  try {
    await api("/api/artifacts/import", { method: "POST", body: formData });
    resultEl.textContent = "Imported and written to graph.";
    fileInput.value = "";
    if (importDomainEl.value === artifactsDomainEl.value) refreshArtifacts();
  } catch (err) {
    resultEl.textContent = `Failed: ${err.message}`;
  }
});

// ── unified snapshots panel ─────────────────────────────────────────
function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function getSelectedComponents(containerSelector) {
  const checkboxes = document.querySelectorAll(`${containerSelector} .snapshot-component, ${containerSelector} .import-component`);
  return Array.from(checkboxes).filter(cb => cb.checked).map(cb => cb.value);
}

async function refreshGuardrail() {
  const container = document.getElementById("snapshot-guardrail");
  try {
    const concepts = await api("/api/help/concepts");
    const snapshot = concepts.find((c) => c.key === "snapshot" && c.source === "cli");
    if (!snapshot) return;
    container.innerHTML = "";
    const badge = snapshot.destructive ? el("span", "destructive-badge", "destructive") : null;
    const head = el("div", "job-card-head", "What this does");
    if (badge) head.appendChild(badge);
    container.appendChild(head);
    container.appendChild(el("div", "dash-note", snapshot.description));
  } catch (err) {
    // guardrail text is best-effort; the confirm() dialogs below still gate the action
  }
}

async function restoreSnapshot(name, components) {
  const componentStr = components.join(",");
  try {
    // First analyze to show warnings
    const analysis = await api(
      `/api/snapshots/${encodeURIComponent(name)}/analyze?components=${encodeURIComponent(componentStr)}`,
      { method: "POST" }
    );

    // Show warnings and ask for confirmation
    let msg = `Restore "${name}" with components: ${components.join(", ")}?\n`;
    if (analysis.warnings && analysis.warnings.length) {
      msg += "\nWarnings:\n";
      for (const w of analysis.warnings) {
        msg += `• ${w}\n`;
      }
    }
    msg += "\nThis action is destructive and cannot be undone. Continue?";

    if (!confirm(msg)) return;

    // Perform restore
    const result = await api(`/api/snapshots/${encodeURIComponent(name)}/restore`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true, components }),
    });

    alert(`Restore complete: ${result.componentsRestored.join(", ")} restored in ${result.elapsedSeconds}s`);
    refreshSnapshots();
    if (artifactsDomainEl.value) refreshStats([artifactsDomainEl.value]);
  } catch (err) {
    alert(`Restore failed: ${err.message}`);
  }
}

async function refreshSnapshots() {
  const tbody = document.querySelector("#snapshots-table tbody");
  const snapshots = await api("/api/snapshots");
  tbody.innerHTML = "";
  for (const snap of snapshots) {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", null, snap.name));
    const componentsText = (snap.components || []).join(", ") || "—";
    tr.appendChild(el("td", null, componentsText));
    tr.appendChild(el("td", null, fmtBytes(snap.sizeBytes)));
    tr.appendChild(el("td", null, fmtTime(snap.createdAt)));

    const actions = el("td");
    const downloadLink = el("a", "btn-link", "Download");
    downloadLink.href = `/api/snapshots/${encodeURIComponent(snap.name)}`;
    actions.appendChild(downloadLink);

    const restoreBtn = el("button", "btn-link", "Restore");
    restoreBtn.addEventListener("click", () => {
      // Default to restoring all available components
      const components = snap.components || ["graph", "registry", "structured", "kg_staging"];
      restoreSnapshot(snap.name, components);
    });
    actions.appendChild(restoreBtn);
    tr.appendChild(actions);
    tbody.appendChild(tr);
  }
}

document.getElementById("create-snapshot-btn").addEventListener("click", async () => {
  const resultEl = document.getElementById("snapshot-create-result");
  resultEl.textContent = "Creating snapshot…";
  try {
    const components = getSelectedComponents("#create-snapshot-components");
    const componentStr = components.join(",");
    const snap = await api(`/api/snapshots?components=${encodeURIComponent(componentStr)}`, { method: "POST" });
    resultEl.textContent = `Created ${snap.name} (${fmtBytes(snap.sizeBytes)}).`;
    refreshSnapshots();
  } catch (err) {
    resultEl.textContent = `Failed: ${err.message}`;
  }
});

document.getElementById("snapshot-import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const fileInput = document.getElementById("snapshot-import-file");
  const file = fileInput.files[0];
  if (!file) return;

  const components = getSelectedComponents("#import-snapshot-components");

  const resultEl = document.getElementById("snapshot-import-result");
  resultEl.textContent = "Uploading and restoring…";
  const formData = new FormData();
  formData.append("confirm", "true");
  formData.append("file", file);
  formData.append("components", components.join(","));

  try {
    const result = await api("/api/snapshots/import", { method: "POST", body: formData });
    resultEl.textContent = `Restored: ${result.componentsRestored.join(", ")}.`;
    fileInput.value = "";
    refreshSnapshots();
  } catch (err) {
    resultEl.textContent = `Failed: ${err.message}`;
  }
});

// ── structured data tab ──────────────────────────────────────────────
function fmtRowCount(n) {
  return n === null || n === undefined ? "—" : String(n);
}

async function refreshStructuredTables() {
  const tbody = document.querySelector("#structured-tables-table tbody");
  const statCardsEl = document.getElementById("structured-stat-cards");
  const domain = structuredTablesDomainEl.value;
  const url = domain ? `/api/structured/tables?domain=${encodeURIComponent(domain)}` : "/api/structured/tables";
  const { tables } = await api(url);

  statCardsEl.innerHTML = "";
  const totalRows = tables.reduce((sum, t) => sum + (t.rowCount || 0), 0);
  const domainCount = new Set(tables.map((t) => t.domain)).size;
  const card = el("div", "stat-card");
  card.appendChild(el("div", "stat-card-title", "Structured store"));
  const statRows = el("div", "stat-card-rows");
  for (const [label, value] of [["tables", tables.length], ["total rows", totalRows], ["domains", domainCount]]) {
    const row = el("div", "stat-card-row");
    row.appendChild(el("span", "stat-key", label));
    row.appendChild(el("span", "stat-value", String(value)));
    statRows.appendChild(row);
  }
  card.appendChild(statRows);
  statCardsEl.appendChild(card);

  tbody.innerHTML = "";
  if (!tables.length) {
    const tr = document.createElement("tr");
    const td = el("td", "dash-empty", "No structured tables registered yet.");
    td.colSpan = 5;
    tr.appendChild(td);
    tbody.appendChild(tr);
    return;
  }

  for (const t of tables) {
    const tr = document.createElement("tr");
    tr.style.cursor = "pointer";
    tr.appendChild(el("td", null, t.tableName));
    tr.appendChild(el("td", null, t.domain));
    tr.appendChild(el("td", null, fmtRowCount(t.rowCount)));
    tr.appendChild(el("td", null, String(t.version)));
    tr.appendChild(el("td", null, fmtTime(t.ingestedAt)));

    const detailTr = document.createElement("tr");
    const detailTd = el("td");
    detailTd.colSpan = 5;
    detailTd.style.display = "none";
    detailTr.appendChild(detailTd);

    tr.addEventListener("click", async () => {
      const showing = detailTd.style.display !== "none";
      if (showing) {
        detailTd.style.display = "none";
        return;
      }
      detailTd.style.display = "";
      detailTd.innerHTML = "Loading…";
      try {
        const schema = await api(
          `/api/structured/tables/${encodeURIComponent(t.tableName)}/schema?domain=${encodeURIComponent(t.domain)}`
        );
        detailTd.innerHTML = "";
        const colTable = el("table", "dash-table");
        const thead = document.createElement("thead");
        thead.innerHTML = "<tr><th>Column</th><th>Type</th><th>Mapping</th></tr>";
        colTable.appendChild(thead);
        const colBody = document.createElement("tbody");
        const mappingByColumn = {};
        for (const m of schema.mappings || []) {
          mappingByColumn[m.column] = `${m.entityClass} (${m.confirmed ? "confirmed" : "proposed"})`;
        }
        for (const c of schema.columns || []) {
          const ctr = document.createElement("tr");
          ctr.appendChild(el("td", null, c.name));
          ctr.appendChild(el("td", null, c.dtype));
          ctr.appendChild(el("td", "dash-note", mappingByColumn[c.name] || "—"));
          colBody.appendChild(ctr);
        }
        colTable.appendChild(colBody);
        detailTd.appendChild(colTable);
      } catch (err) {
        detailTd.innerHTML = "";
        detailTd.appendChild(el("div", "dash-note", `Failed to load schema: ${err.message}`));
      }
    });

    tbody.appendChild(tr);
    tbody.appendChild(detailTr);
  }
}

document.getElementById("structured-ingest-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const resultEl = document.getElementById("structured-ingest-result");
  const fileInput = document.getElementById("structured-ingest-file");
  const file = fileInput.files[0];
  if (!file) return;
  resultEl.textContent = "Ingesting…";
  const formData = new FormData();
  formData.append("domain", structuredIngestDomainEl.value);
  formData.append("file", file);
  const table = document.getElementById("structured-ingest-table").value.trim();
  if (table) formData.append("table", table);
  const sheet = document.getElementById("structured-ingest-sheet").value.trim();
  if (sheet) formData.append("sheet", sheet);
  formData.append("headerRow", document.getElementById("structured-ingest-header-row").value || "0");
  formData.append("force", document.getElementById("structured-ingest-force").checked ? "true" : "false");
  try {
    const result = await api("/api/structured/ingest", { method: "POST", body: formData });
    resultEl.textContent = result.status === "skipped"
      ? "Unchanged — skipped (use Force to re-ingest)."
      : `Ingested ${result.tables.length} table(s).`;
    fileInput.value = "";
    refreshStructuredTables();
  } catch (err) {
    resultEl.textContent = `Failed: ${err.message}`;
  }
});

document.getElementById("structured-sql-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const resultEl = document.getElementById("structured-sql-result");
  const sql = document.getElementById("structured-sql-input").value.trim();
  if (!sql) return;
  resultEl.innerHTML = "Running…";
  try {
    const result = await api("/api/structured/sql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sql }),
    });
    resultEl.innerHTML = "";
    const rows = result.rows || [];
    if (!rows.length) {
      resultEl.appendChild(el("p", "dash-empty", "Query returned no rows."));
      return;
    }
    const wrap = el("div", "table-scroll");
    const table = el("table", "dash-table");
    const thead = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const key of Object.keys(rows[0])) headRow.appendChild(el("th", null, key));
    thead.appendChild(headRow);
    table.appendChild(thead);
    const tableBody = document.createElement("tbody");
    for (const row of rows) {
      const tr = document.createElement("tr");
      for (const key of Object.keys(rows[0])) tr.appendChild(el("td", null, String(row[key])));
      tableBody.appendChild(tr);
    }
    table.appendChild(tableBody);
    wrap.appendChild(table);
    resultEl.appendChild(wrap);
  } catch (err) {
    resultEl.innerHTML = "";
    resultEl.appendChild(el("div", "dash-note", `Failed: ${err.message}`));
  }
});


// ── polling ──────────────────────────────────────────────────────────
initTabs();
loadDomains();
loadStructuredDomains();
refreshActiveJobs();
refreshCompletedJobs();
refreshSnapshots();
refreshStructuredSnapshots();
refreshGuardrail();
setInterval(refreshActiveJobs, 10000);
setInterval(refreshCompletedJobs, 5000);
