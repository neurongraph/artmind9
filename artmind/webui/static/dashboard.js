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
    await api("/api/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, path }),
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
async function refreshActiveJobs() {
  const container = document.getElementById("active-jobs");
  const jobs = await api("/api/jobs/active");
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
    const fileList = el("ul", "file-list");
    for (const f of job.files) {
      const step = f.currentStep ? ` → ${f.currentStep}` : "";
      fileList.appendChild(el("li", null, `${f.status}: ${f.filename.split("/").pop()}${step}`));
    }
    card.appendChild(fileList);
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

async function showChunkGrid(container, jobId, domain, docName) {
  container.innerHTML = "Loading chunks…";
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
    if (a.inGraph) head.appendChild(el("span", "in-graph-badge", "in graph"));
    card.appendChild(head);
    card.appendChild(el("div", "dash-note",
      `${a.entityCount} entities · ${a.propertyCount} properties · ${a.relationshipCount} relationships`));
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

// ── snapshots panel ──────────────────────────────────────────────────
function fmtBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function confirmWipe(snapshotName) {
  return confirm(
    `Restoring "${snapshotName}" will DELETE ALL current data in the Neo4j database ` +
    `and replace it with the snapshot's contents. This cannot be undone. Continue?`
  );
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

async function restoreSnapshot(name) {
  if (!confirmWipe(name)) return;
  try {
    const summary = await api(`/api/snapshots/${encodeURIComponent(name)}/restore`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: true }),
    });
    alert(`Restored ${name}: ${summary.relationshipCount} relationships, ` +
      `${Object.values(summary.nodeCounts || {}).reduce((a, b) => a + b, 0)} nodes.`);
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
    tr.appendChild(el("td", null, fmtBytes(snap.sizeBytes)));
    tr.appendChild(el("td", null, fmtTime(snap.createdAt)));

    const actions = el("td");
    const downloadLink = el("a", "btn-link", "Download");
    downloadLink.href = `/api/snapshots/${encodeURIComponent(snap.name)}`;
    actions.appendChild(downloadLink);
    const restoreBtn = el("button", "btn-link", "Restore");
    restoreBtn.addEventListener("click", () => restoreSnapshot(snap.name));
    actions.appendChild(restoreBtn);
    tr.appendChild(actions);
    tbody.appendChild(tr);
  }
}

document.getElementById("create-snapshot-btn").addEventListener("click", async () => {
  const resultEl = document.getElementById("snapshot-create-result");
  resultEl.textContent = "Exporting…";
  try {
    const snap = await api("/api/snapshots", { method: "POST" });
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
  if (!confirmWipe(file.name)) return;
  const resultEl = document.getElementById("snapshot-import-result");
  resultEl.textContent = "Uploading and restoring…";
  const formData = new FormData();
  formData.append("confirm", "true");
  formData.append("file", file);
  try {
    const summary = await api("/api/snapshots/import", { method: "POST", body: formData });
    resultEl.textContent = `Restored: ${summary.relationshipCount} relationships.`;
    fileInput.value = "";
    refreshSnapshots();
  } catch (err) {
    resultEl.textContent = `Failed: ${err.message}`;
  }
});

// ── polling ──────────────────────────────────────────────────────────
loadDomains();
refreshActiveJobs();
refreshCompletedJobs();
refreshSnapshots();
refreshGuardrail();
setInterval(refreshActiveJobs, 2000);
setInterval(refreshCompletedJobs, 5000);
