"use strict";

// Benchmark panel: parse a Q&A markdown file, run each question through the
// artmind query agent one after another, and record every answer. Reuses
// el()/api()/fmtTime()/initTabs() from dashboard.js (loaded first) and the
// shared #job-drawer chrome (openJobDrawer/closeJobDrawer) for question and
// run detail, rather than duplicating drawer machinery.

const benchmarkPathEl = document.getElementById("benchmark-path");
const benchmarkStartBtn = document.getElementById("benchmark-start-btn");
const benchmarkPreviewEl = document.getElementById("benchmark-preview");
let benchmarkPreviewedPath = null;

benchmarkPathEl.addEventListener("input", () => {
  benchmarkStartBtn.disabled = true;
});

document.getElementById("benchmark-preview-btn").addEventListener("click", async () => {
  const path = benchmarkPathEl.value.trim();
  if (!path) return;
  benchmarkPreviewEl.textContent = "Parsing…";
  try {
    const result = await api("/api/benchmark/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    benchmarkPreviewEl.innerHTML = "";
    benchmarkPreviewEl.appendChild(el("div", null, `Parsed ${result.questionCount} question(s).`));
    for (const q of result.questions.slice(0, 5)) {
      const label = q.title ? `${q.qid} — ${q.title}` : q.qid;
      const preview = q.question.length > 140 ? `${q.question.slice(0, 140)}…` : q.question;
      benchmarkPreviewEl.appendChild(el("div", "dash-note", `${label}: ${preview}`));
    }
    if (result.questionCount > 5) {
      benchmarkPreviewEl.appendChild(el("div", "dash-note", `…and ${result.questionCount - 5} more.`));
    }
    benchmarkPreviewedPath = path;
    benchmarkStartBtn.disabled = result.questionCount === 0;
  } catch (err) {
    benchmarkPreviewEl.textContent = `Preview failed: ${err.message}`;
    benchmarkStartBtn.disabled = true;
  }
});

document.getElementById("benchmark-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const path = benchmarkPathEl.value.trim();
  if (!path || path !== benchmarkPreviewedPath) {
    alert("Preview the file first (or the path changed since the last preview).");
    return;
  }
  const domainHint = document.getElementById("benchmark-domain-hint").value.trim();
  const backend = document.getElementById("benchmark-backend").value;
  const name = document.getElementById("benchmark-name").value.trim();
  benchmarkStartBtn.disabled = true;
  try {
    await api("/api/benchmark/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        path,
        domainHint: domainHint || null,
        backend,
        name: name || null,
      }),
    });
    benchmarkPreviewEl.textContent = "";
    benchmarkPreviewedPath = null;
    refreshActiveBenchmarkRuns();
  } catch (err) {
    alert(`Start run failed: ${err.message}`);
    benchmarkStartBtn.disabled = false;
  }
});

// ── active runs ──────────────────────────────────────────────────────
async function cancelBenchmarkRun(runId) {
  if (!confirm("Cancel this benchmark run?")) return;
  try {
    await api(`/api/benchmark/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
    refreshActiveBenchmarkRuns();
    refreshCompletedBenchmarkRuns();
  } catch (err) {
    alert(`Cancel failed: ${err.message}`);
  }
}

async function refreshActiveBenchmarkRuns() {
  const container = document.getElementById("benchmark-active-runs");
  const runs = await api("/api/benchmark/runs/active");

  const countEl = document.getElementById("benchmark-active-tab-count");
  if (countEl) countEl.textContent = runs.length ? `· ${runs.length}` : "";

  if (!runs.length) {
    container.innerHTML = '<p class="dash-empty">No active or queued benchmark runs.</p>';
    return;
  }
  container.innerHTML = "";

  for (const run of runs) {
    const card = el("div", "job-card");
    const pct = run.questionCount ? Math.round((100 * run.processedCount) / run.questionCount) : 0;
    const label = run.name || `${run.runId.slice(0, 10)}…`;
    card.appendChild(el("div", "job-card-head",
      `${label} · ${run.domainHint || "auto-routed"} · ${run.status.toUpperCase()}`));

    const bar = el("div", "progress-bar");
    const fill = el("div", "progress-fill");
    fill.style.width = `${pct}%`;
    bar.appendChild(fill);
    card.appendChild(bar);
    card.appendChild(el("div", "dash-note", `${run.processedCount}/${run.questionCount} questions`));

    const viewBtn = el("button", "btn-link", "View");
    viewBtn.addEventListener("click", () => showBenchmarkRunDetail(run.runId));
    card.appendChild(viewBtn);
    const cancelBtn = el("button", "btn-link", "Cancel");
    cancelBtn.addEventListener("click", () => cancelBenchmarkRun(run.runId));
    card.appendChild(cancelBtn);

    container.appendChild(card);
  }
}

// ── completed runs ───────────────────────────────────────────────────
async function refreshCompletedBenchmarkRuns() {
  const tbody = document.querySelector("#benchmark-completed-table tbody");
  const runs = await api("/api/benchmark/runs/completed");
  tbody.innerHTML = "";
  for (const run of runs) {
    const tr = document.createElement("tr");
    tr.appendChild(el("td", null, run.name || `${run.runId.slice(0, 10)}…`));
    tr.appendChild(el("td", null, run.domainHint || "—"));
    tr.appendChild(el("td", null, `${run.processedCount}/${run.questionCount}`));
    tr.appendChild(el("td", `status-${run.status}`, run.status));
    tr.appendChild(el("td", null, fmtTime(run.completedAt)));

    const actions = el("td");
    const viewBtn = el("button", "btn-link", "View");
    viewBtn.addEventListener("click", () => showBenchmarkRunDetail(run.runId));
    actions.appendChild(viewBtn);
    const mdLink = el("a", "btn-link", "MD");
    mdLink.href = `/api/benchmark/runs/${encodeURIComponent(run.runId)}/export?format=md`;
    actions.appendChild(mdLink);
    const jsonLink = el("a", "btn-link", "JSON");
    jsonLink.href = `/api/benchmark/runs/${encodeURIComponent(run.runId)}/export?format=json`;
    actions.appendChild(jsonLink);
    tr.appendChild(actions);
    tbody.appendChild(tr);
  }
}

// ── detail drawer (shared #job-drawer chrome from dashboard.js) ────────
async function showBenchmarkRunDetail(runId) {
  const body = document.getElementById("job-drawer-body");
  body.innerHTML = "Loading…";
  openJobDrawer();
  try {
    const run = await api(`/api/benchmark/runs/${encodeURIComponent(runId)}`);
    body.innerHTML = "";
    body.appendChild(el("div", "tool-head", run.name || run.runId));
    body.appendChild(el("div", "dash-note",
      `${run.status} · ${run.processedCount}/${run.questionCount} · domain: ${run.domainHint || "auto-routed"}`));
    if (run.errorMessage) body.appendChild(el("div", "dash-note", `Error: ${run.errorMessage}`));

    for (const q of run.questions) {
      const row = el("div", "tool-card");
      const label = q.title ? `${q.qid} — ${q.title}` : q.qid;
      row.appendChild(el("div", "tool-head", `${label} · ${q.status}`));
      if (q.errorMessage) row.appendChild(el("div", "dash-note", q.errorMessage));
      const viewBtn = el("button", "btn-link", "View answer");
      viewBtn.addEventListener("click", () => showBenchmarkQuestionDetail(runId, q.seq));
      row.appendChild(viewBtn);
      body.appendChild(row);
    }

    const exportRow = el("div", "dash-note");
    const mdLink = el("a", "btn-link", "Export markdown");
    mdLink.href = `/api/benchmark/runs/${encodeURIComponent(runId)}/export?format=md`;
    exportRow.appendChild(mdLink);
    const jsonLink = el("a", "btn-link", "Export JSON");
    jsonLink.href = `/api/benchmark/runs/${encodeURIComponent(runId)}/export?format=json`;
    exportRow.appendChild(jsonLink);
    body.appendChild(exportRow);
  } catch (err) {
    body.textContent = `Failed to load: ${err.message}`;
  }
}

async function showBenchmarkQuestionDetail(runId, seq) {
  const body = document.getElementById("job-drawer-body");
  body.innerHTML = "Loading…";
  openJobDrawer();
  try {
    const q = await api(`/api/benchmark/runs/${encodeURIComponent(runId)}/questions/${seq}`);
    body.innerHTML = "";
    body.appendChild(el("div", "tool-head", q.title ? `${q.qid} — ${q.title}` : q.qid));
    body.appendChild(el("div", "dash-note",
      `${q.status} · ${q.turns || 0} turns · ${q.durationS || 0}s · $${(q.costUsd || 0).toFixed(4)}`));

    body.appendChild(el("div", "dash-subhead", "Question"));
    body.appendChild(el("div", null, q.question));

    body.appendChild(el("div", "dash-subhead", "Answer"));
    body.appendChild(el("pre", null, q.answerText || `(no answer — ${q.errorMessage || q.status})`));

    if (q.evalComment) {
      body.appendChild(el("div", "dash-subhead", "Grading notes (not shown to artmind)"));
      body.appendChild(el("div", "dash-note", q.evalComment));
    }

    if (q.trace && q.trace.length) {
      body.appendChild(el("div", "dash-subhead", "Trace"));
      for (const t of q.trace) {
        const card = el("div", "tool-card");
        if (t.type === "tool_call") {
          card.appendChild(el("div", "tool-name", t.name || "tool"));
          card.appendChild(el("pre", null, typeof t.input === "string" ? t.input : JSON.stringify(t.input)));
        } else {
          card.appendChild(el("div", "tool-result-label", "result"));
          card.appendChild(el("pre", null, t.content ?? ""));
        }
        body.appendChild(card);
      }
    }
  } catch (err) {
    body.textContent = `Failed to load: ${err.message}`;
  }
}

// ── polling ──────────────────────────────────────────────────────────
refreshActiveBenchmarkRuns();
refreshCompletedBenchmarkRuns();
setInterval(refreshActiveBenchmarkRuns, 10000);
setInterval(refreshCompletedBenchmarkRuns, 10000);
