# Admin Dashboard Layout Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Regroup the admin ingest dashboard from six stacked full-width panels into a two-column working layout — a left rail for the things you *do* (Ingest, Embeddings, Browse), a right column for the things you *watch* (Jobs, Add extracted KG), and a visually separated maintenance zone for Snapshots — plus an inline job → file → chunk drill-down on active jobs.

**Architecture:** Pure frontend restructure of three files (`dashboard.html`, `dashboard.css`, `dashboard.js`) plus one small backend addition (the dashboard ingest endpoint cannot currently create stage-only jobs even though the whole backend supports them). The HTML restructure **re-parents existing elements and preserves every `id`** so the shipped JS keeps working; new behaviour (tabs, drill-down) is added on top. Active-job polling currently rebuilds its container every 2s, which would destroy an expanded drill-down, so expansion state is tracked in a `Set` and restored after each rebuild.

**Tech Stack:** Jinja2 template, vanilla JS (no framework, no build step), hand-written CSS using the theme variables in `static/style.css`. FastAPI + Pydantic for the one backend change. pytest + `TestClient` for the backend test; the JS has no unit-test harness in this repo, so its verification is a scripted manual checklist.

---

## Reconciliation: what shipped vs. what the mockup assumed

The mockup that motivated this plan was drawn **before** the staging→commit work landed (commit `da880be`). Verified current state:

| Area | State in the tree today |
|---|---|
| `dashboard.html` | **Unchanged** — still the original six linear `<section class="dash-panel">` blocks |
| `dashboard.js` | Only `refreshArtifacts` changed (lines 279-321): renders a `state-badge` and a "Write to graph" button on staged docs |
| `dashboard.css` | `.state-badge`, `.state-badge.in-graph`, `.state-badge.staged` added (lines 71-76); the old `.in-graph-badge` rule was removed |
| Commit endpoint | `POST /api/artifacts/{domain}/{doc}/write-to-graph` exists (`dashboard_routes.py:287`) |
| `stage_only` | Fully wired through `db.py` → `jobs.py` → `worker.py` → `ingest.py`, and exposed on the CLI |

**Three deltas from the mockup, each changing what to build:**

1. **Browse already has a commit action.** The mockup's Browse was read-only. It now renders a state badge and a "Write to graph" button. Task 5 must **keep** that behaviour when Browse moves into the left rail — do not revert `refreshArtifacts` to the mockup version.

2. **"Pull from repo" is no longer a dead end.** The mockup annotated Pull as staging with no way to finish. There is now a commit path (Pull stages → the doc appears in Browse as `staged` → "Write to graph" commits it). The panel copy must say that instead of warning about a gap. Import still commits immediately — that asymmetry is real and the copy should state it plainly.

3. **The dashboard cannot create stage-only jobs.** `IngestRequest` (`dashboard_routes.py:52-54`) has only `domain` and `path`, and `api_ingest` calls `_create_job(batch_files, domain=...)` without `stage_only`. The CLI can stage; the UI cannot. Task 1 closes this so the Ingest panel can offer the checkbox.

**One deliberate deviation from the mockup:** the mockup drew a "Retry failed" button on *active* job cards. `_retry_job` (`jobs.py`) has no job-status guard and resets the parent job to `queued`, which would race the worker mid-loop on a `processing` job. Retry therefore stays on the Completed tab only, where it already works. Do not add it to active cards.

**Out of scope (deliberate):** the stat-card rendering in `refreshStats` is left as-is. The mockup restyled it to large single numbers, but that is cosmetic, `structural_metadata` returns label/count rows that the current card shape fits, and changing it adds risk without changing the grouping this plan is about.

---

## Background the implementer needs

- Read `CLAUDE.md` first. The critical fact for this plan: **static assets and templates reach the running admin UI through the run folder.** After editing, run `just dev-install` (which runs `artmind init`) or the UI serves stale files. A running `serve`/admin daemon also holds old code — `just dev-stop-daemons` first.
- Run the test suite with `just dev-test`. Tests are hermetic (no Neo4j, no network).
- Neo4j is **not** started automatically. The user starts it manually. Before any live-UI step, check `nc -z localhost 7687` and ask the user if it is not up.
- Theme variables (`--bg`, `--bg-elev`, `--bg-inset`, `--text`, `--text-muted`, `--border`, `--accent`, `--error`) are defined in `static/style.css` for both `[data-theme="light"]` and `[data-theme="dark"]`. Every colour added in this plan must come from a variable so both themes work.
- Reusable classes that already exist — do **not** redefine them: `.dot` / `.dot.done` / `.dot.error` / `.dot.running` (`style.css:308-311`), `.btn-link` (`dashboard.css:59`), `.progress-bar` / `.progress-fill` (`dashboard.css:77-81`), `.chunk-grid` / `.chunk-row` / `.chunk-seq` (`dashboard.css:93-98`), `.state-badge` (`dashboard.css:71-76`), `.drawer` (`style.css:280`).

### The ID contract (the single biggest risk in this plan)

`dashboard.js` resolves these by `id`. Task 2 moves elements between parents but **every one of these ids must survive verbatim**, or the JS silently breaks:

```
theme-toggle  stat-cards
ingest-form  ingest-domain  ingest-path
embed-domain  embed-btn  embed-result
artifacts-domain  artifact-cards
active-jobs  completed-table
pull-kg-form  pull-repo  pull-repo-path  pull-domain  pull-result
import-kg-form  import-doc  import-domain  import-file  import-result
snapshot-guardrail  create-snapshot-btn  snapshot-create-result
snapshots-table  snapshot-import-form  snapshot-import-file  snapshot-import-result
job-drawer  job-drawer-close  job-drawer-body
```

Task 2 ends with an automated check that all of them still resolve.

---

## File Structure

- `artmind/webui/dashboard_routes.py` — add `stage_only` to `IngestRequest` and pass it to `_create_job`.
- `artmind/webui/templates/dashboard.html` — full restructure into stat strip + two-column grid + maintenance zone; adds tab markup and one new checkbox. All existing ids preserved.
- `artmind/webui/static/style.css` — add a `--warn` theme token (both themes) for the maintenance zone.
- `artmind/webui/static/dashboard.css` — layout (grid/columns/panel cards), tab styles, file-row drill-down styles, maintenance zone.
- `artmind/webui/static/dashboard.js` — generic tab controller, active-tab count, inline drill-down with poll-safe expansion state, quiet chunk refresh, stage-only submit field.
- `test/test_webui_admin_api.py` — test for the stage-only ingest payload.

---

## Task 1: Let the dashboard create stage-only ingest jobs

**Files:**
- Modify: `artmind/webui/dashboard_routes.py:52-54` (`IngestRequest`) and the `api_ingest` handler (`_create_job` call)
- Test: `test/test_webui_admin_api.py`

- [ ] **Step 1: Write the failing test**

Add to `test/test_webui_admin_api.py`:

```python
def test_ingest_passes_stage_only_to_create_job(monkeypatch, tmp_path):
    from artmind.webui import dashboard_routes

    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")

    seen = {}

    def fake_create_job(batch_files, domain="general", force=False, stage_only=False):
        seen["stage_only"] = stage_only
        seen["domain"] = domain
        return "job-1"

    monkeypatch.setattr(dashboard_routes, "_create_job", fake_create_job)
    monkeypatch.setattr(dashboard_routes, "_ensure_worker_running", lambda: None)

    resp = _client().post(
        "/api/ingest",
        json={"domain": "mydomain", "path": str(f), "stageOnly": True},
    )
    assert resp.status_code == 200
    assert seen["stage_only"] is True
    assert seen["domain"] == "mydomain"


def test_ingest_defaults_stage_only_false(monkeypatch, tmp_path):
    from artmind.webui import dashboard_routes

    f = tmp_path / "b.txt"
    f.write_text("x", encoding="utf-8")

    seen = {}

    def fake_create_job(batch_files, domain="general", force=False, stage_only=False):
        seen["stage_only"] = stage_only
        return "job-2"

    monkeypatch.setattr(dashboard_routes, "_create_job", fake_create_job)
    monkeypatch.setattr(dashboard_routes, "_ensure_worker_running", lambda: None)

    resp = _client().post("/api/ingest", json={"domain": "mydomain", "path": str(f)})
    assert resp.status_code == 200
    assert seen["stage_only"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --group dev pytest test/test_webui_admin_api.py::test_ingest_passes_stage_only_to_create_job -v`
Expected: FAIL — `_create_job` is called without `stage_only`, so `seen["stage_only"]` is `False`, not `True`.

- [ ] **Step 3: Add the field and pass it through**

In `artmind/webui/dashboard_routes.py`, replace the `IngestRequest` class (lines 52-54) with:

```python
class IngestRequest(BaseModel):
    domain: str
    path: str
    stage_only: bool = Field(False, alias="stageOnly")

    model_config = {"populate_by_name": True}
```

(`Field` is already imported at the top of the file — it is used by `RetryRequest`. The `populate_by_name` config mirrors `RetryRequest`/`PullKgRequest`.)

In the `api_ingest` handler, change the `_create_job` call to:

```python
        job_id = _create_job(batch_files, domain=payload.domain, stage_only=payload.stage_only)
```

- [ ] **Step 4: Run the tests**

Run: `uv run --group dev pytest test/test_webui_admin_api.py -v`
Expected: PASS, including the two new tests and all pre-existing ones.

- [ ] **Step 5: Commit**

```bash
git add artmind/webui/dashboard_routes.py test/test_webui_admin_api.py
git commit -m "feat(admin): accept stageOnly on the dashboard ingest endpoint

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Restructure `dashboard.html` into the two-column layout

Re-parent existing markup into a stat strip, a two-column grid, and a maintenance zone. Add tab bars (markup only — the controller comes in Task 4) and the stage-only checkbox. **Every existing id is preserved.**

**Files:**
- Modify: `artmind/webui/templates/dashboard.html` (replace the whole `<main>` block, lines 32-151)

- [ ] **Step 1: Replace the `<main>` block**

In `artmind/webui/templates/dashboard.html`, replace everything from `<main class="dash">` (line 32) through its closing `</main>` (line 151) with:

```html
  <main class="dash">
    <div class="stat-row" id="stat-cards"></div>

    <div class="dash-grid">
      <div class="col-side">

        <section class="dash-panel" id="ingest-panel">
          <h2>Ingest</h2>
          <p class="dash-hint">Raw file → job → worker → graph.</p>
          <form id="ingest-form" class="dash-form stacked">
            <label>
              Domain
              <select id="ingest-domain" required></select>
            </label>
            <label>
              Path
              <input id="ingest-path" type="text" placeholder="/path/to/file/or/folder" required>
            </label>
            <label class="checkbox">
              <input id="ingest-stage-only" type="checkbox">
              Stage only — extract, don't write to graph
            </label>
            <button type="submit" class="btn-primary">Ingest</button>
          </form>
        </section>

        <section class="dash-panel" id="embed-panel">
          <h2>Embeddings</h2>
          <p class="dash-hint">Backfill after a snapshot restore or a consolidate run.</p>
          <div class="dash-form stacked">
            <label>
              Backfill for domain
              <select id="embed-domain"></select>
            </label>
            <button id="embed-btn" type="button" class="btn-secondary">Run backfill</button>
          </div>
          <span id="embed-result" class="dash-note"></span>
        </section>

        <section class="dash-panel" id="artifacts-panel">
          <h2>Browse</h2>
          <p class="dash-hint">Staged extraction output for one domain.</p>
          <div class="dash-form stacked">
            <label>
              Domain
              <select id="artifacts-domain"></select>
            </label>
          </div>
          <div id="artifact-cards" class="job-cards" style="margin-top: 12px;">
            <p class="dash-empty">No documents extracted yet for this domain.</p>
          </div>
        </section>

      </div>

      <div class="col-main">

        <section class="dash-panel" id="jobs-panel">
          <h2>Jobs</h2>
          <div class="tabs" data-tab-group="jobs">
            <button type="button" class="tab active" data-tab="active">
              Active <span id="active-tab-count" class="tab-count"></span>
            </button>
            <button type="button" class="tab" data-tab="completed">Completed</button>
          </div>

          <div class="tab-panel active" data-tab-group="jobs" data-tab-panel="active">
            <div id="active-jobs" class="job-cards">
              <p class="dash-empty">No active or queued jobs.</p>
            </div>
          </div>

          <div class="tab-panel" data-tab-group="jobs" data-tab-panel="completed">
            <div class="table-scroll">
              <table class="dash-table" id="completed-table">
                <thead>
                  <tr><th>Job</th><th>Domain</th><th>Files</th><th>Status</th><th>Completed</th><th></th></tr>
                </thead>
                <tbody></tbody>
              </table>
            </div>
          </div>
        </section>

        <section class="dash-panel" id="add-kg-panel">
          <h2>Add extracted KG</h2>
          <p class="dash-hint">Bring in artifacts extracted elsewhere. No job, no worker.</p>
          <div class="tabs" data-tab-group="addkg">
            <button type="button" class="tab active" data-tab="pull">Pull from repo</button>
            <button type="button" class="tab" data-tab="import">Import bundle</button>
          </div>

          <div class="tab-panel active" data-tab-group="addkg" data-tab-panel="pull">
            <form id="pull-kg-form" class="dash-form">
              <label class="grow">
                Repo URL
                <input id="pull-repo" type="text" placeholder="git@github.com:acme/kg-store.git" required>
              </label>
              <label class="grow">
                Repo path
                <input id="pull-repo-path" type="text" placeholder="data/kg/sales_collateral" required>
              </label>
              <label>
                Domain
                <select id="pull-domain"></select>
              </label>
              <button type="submit" class="btn-secondary">Pull</button>
            </form>
            <p class="dash-hint">Stages files only. They appear in Browse as <strong>staged</strong> — use “Write to graph” there to commit.</p>
            <span id="pull-result" class="dash-note"></span>
          </div>

          <div class="tab-panel" data-tab-group="addkg" data-tab-panel="import">
            <form id="import-kg-form" class="dash-form">
              <label>
                Doc name
                <input id="import-doc" type="text" placeholder="myfile" required>
              </label>
              <label>
                Domain
                <select id="import-domain"></select>
              </label>
              <label class="grow">
                Bundle .zip
                <input id="import-file" type="file" accept=".zip" required>
              </label>
              <button type="submit" class="btn-secondary">Import</button>
            </form>
            <p class="dash-hint">Writes to the graph immediately on upload.</p>
            <span id="import-result" class="dash-note"></span>
          </div>
        </section>

      </div>
    </div>

    <section class="dash-panel dash-zone-maint" id="snapshots-panel">
      <h2>Maintain — snapshots</h2>
      <p class="dash-hint">Infrequent and destructive. Restoring replaces the entire graph.</p>
      <div id="snapshot-guardrail" class="guardrail-callout"></div>
      <div class="zone-maint-body">
        <div>
          <div class="table-scroll">
            <table class="dash-table" id="snapshots-table">
              <thead>
                <tr><th>Name</th><th>Size</th><th>Created</th><th></th></tr>
              </thead>
              <tbody></tbody>
            </table>
          </div>
        </div>
        <div>
          <button id="create-snapshot-btn" type="button" class="btn-primary">Create snapshot</button>
          <span id="snapshot-create-result" class="dash-note"></span>
          <h3 class="dash-subhead">Upload and restore</h3>
          <form id="snapshot-import-form" class="dash-form stacked">
            <label>
              Snapshot .tar.gz
              <input id="snapshot-import-file" type="file" accept=".tar.gz" required>
            </label>
            <button type="submit" class="btn-secondary">Upload &amp; restore</button>
          </form>
          <span id="snapshot-import-result" class="dash-note"></span>
        </div>
      </div>
    </section>
  </main>
```

Leave the `<header class="topbar">` block (lines 19-30), the `<aside id="job-drawer">` block (lines 153-163), and the `<script src="/static/dashboard.js">` tag unchanged.

- [ ] **Step 2: Verify no id was lost**

Run this from the repo root — it extracts every `getElementById`/`querySelector` id the JS depends on and confirms each exists in the template:

```bash
cd /Users/surjitdas/Projects/artmind9
python3 - <<'PY'
import re, pathlib
js = pathlib.Path("artmind/webui/static/dashboard.js").read_text()
html = pathlib.Path("artmind/webui/templates/dashboard.html").read_text()
ids = set(re.findall(r'getElementById\("([^"]+)"\)', js))
ids |= set(re.findall(r'querySelector\("#([A-Za-z0-9_-]+)', js))
missing = sorted(i for i in ids if f'id="{i}"' not in html)
print("JS depends on", len(ids), "ids")
print("MISSING:", missing if missing else "none — all ids present")
raise SystemExit(1 if missing else 0)
PY
```

Expected: `MISSING: none — all ids present`, exit code 0. If anything is listed, add that element back before continuing.

- [ ] **Step 3: Commit**

```bash
git add artmind/webui/templates/dashboard.html
git commit -m "refactor(admin-ui): restructure dashboard into two-column layout with zones

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Layout, tab, drill-down, and maintenance-zone CSS

**Files:**
- Modify: `artmind/webui/static/style.css` (add `--warn` to both theme blocks)
- Modify: `artmind/webui/static/dashboard.css` (replace the layout header, append new rules)

- [ ] **Step 1: Add the `--warn` theme token**

In `artmind/webui/static/style.css`, add one line to each theme block. In `:root[data-theme="light"]` (after the `--error: #b3423f;` line):

```css
  --warn: #b8863b;
```

In `:root[data-theme="dark"]` (after its `--error: #e07a77;` line):

```css
  --warn: #d1a25e;
```

- [ ] **Step 2: Replace the layout block at the top of `dashboard.css`**

In `artmind/webui/static/dashboard.css`, replace lines 1-21 (from the `/* ── Lane B dashboard layout ... */` comment through the `.dash-note` rule) with:

```css
/* ── Lane B dashboard layout (uses style.css's theme variables) ─────── */
.dash {
  max-width: 80rem;
  margin: 0 auto;
  padding: 88px 20px 60px;
}
.dash-grid {
  display: grid;
  grid-template-columns: 300px 1fr;
  gap: 20px;
  align-items: start;
}
.col-side {
  position: sticky;
  top: 88px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.col-main { display: flex; flex-direction: column; gap: 20px; }
@media (max-width: 900px) {
  .dash-grid { grid-template-columns: 1fr; }
  .col-side { position: static; }
}

.dash-panel {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--bg-elev);
  padding: 16px 16px 18px;
}
.dash-panel h2 {
  margin: 0 0 4px;
  font-size: 13px;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.dash-hint {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--text-muted);
}
.dash-subhead {
  margin: 18px 0 8px;
  font-size: 12.5px; font-weight: 600;
  color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.03em;
}
.dash-empty { color: var(--text-muted); font-size: 13px; }
.dash-note { color: var(--text-muted); font-size: 12.5px; }
.table-scroll { overflow-x: auto; }
```

- [ ] **Step 3: Append the new component rules**

Append to the end of `artmind/webui/static/dashboard.css`:

```css
/* ── spacing: .dash is no longer a flex container with gap, so the stat
      strip needs its own bottom margin (overrides the earlier .stat-row) ── */
.stat-row { margin-bottom: 20px; }

/* ── stacked form variant (sidebar panels) ───────────────────────────── */
.dash-form.stacked { flex-direction: column; align-items: stretch; gap: 10px; }
.dash-form.stacked label { width: 100%; }
.dash-form.stacked input[type="text"],
.dash-form.stacked input[type="file"],
.dash-form.stacked select { width: 100%; }
.dash-form label.checkbox {
  flex-direction: row; align-items: center; gap: 7px;
  font-size: 12px; color: var(--text-muted); cursor: pointer;
}
.dash-form label.checkbox input { width: auto; }

/* ── tabs ────────────────────────────────────────────────────────────── */
.tabs {
  display: flex; gap: 2px; margin: 10px 0 14px;
  background: var(--bg-inset); border-radius: 8px; padding: 3px;
}
.tab {
  flex: 1; text-align: center; padding: 6px 8px; border-radius: 6px;
  font-size: 12.5px; font-weight: 600; color: var(--text-muted);
}
.tab.active { background: var(--bg-elev); color: var(--text); }
.tab:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.tab-count { font-variant-numeric: tabular-nums; opacity: 0.75; }
.tab-panel { display: none; }
.tab-panel.active { display: block; }

/* ── active job file rows + chunk drill-down ─────────────────────────── */
.file-rows { margin-top: 8px; border-top: 1px solid var(--border); padding-top: 6px; }
.file-row + .file-row { margin-top: 2px; }
.file-row summary {
  list-style: none; cursor: pointer;
  display: flex; align-items: center; gap: 8px;
  padding: 5px 6px; border-radius: 6px; font-size: 12.5px;
}
.file-row summary::-webkit-details-marker { display: none; }
.file-row summary:hover { background: var(--bg-inset); }
.file-row summary:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.file-row[open] summary { font-weight: 600; }
.file-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.chunk-drill { padding: 6px 6px 4px 22px; }

/* ── maintenance zone ────────────────────────────────────────────────── */
.dash-zone-maint {
  margin-top: 26px;
  border-color: color-mix(in srgb, var(--warn) 45%, var(--border));
  background: color-mix(in srgb, var(--warn) 6%, var(--bg-elev));
}
.dash-zone-maint h2 { color: var(--warn); }
.zone-maint-body { display: grid; grid-template-columns: 1.4fr 1fr; gap: 20px; margin-top: 12px; }
@media (max-width: 900px) { .zone-maint-body { grid-template-columns: 1fr; } }
```

- [ ] **Step 4: Commit**

```bash
git add artmind/webui/static/style.css artmind/webui/static/dashboard.css
git commit -m "feat(admin-ui): layout, tab, drill-down and maintenance-zone styles

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Generic tab controller

One controller drives both tab groups via `data-` attributes — do not write per-panel handlers.

**Files:**
- Modify: `artmind/webui/static/dashboard.js` (add near the helpers, after `api()` which ends at line 31)

- [ ] **Step 1: Add the controller**

In `artmind/webui/static/dashboard.js`, insert after the `api()` helper (after line 31, before the `// ── domain picker ──` comment at line 33):

```javascript
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
```

- [ ] **Step 2: Call it at startup**

At the bottom of the file, in the `// ── polling ──` block (currently line 470 onward), add `initTabs();` as the first call — before `loadDomains();`:

```javascript
// ── polling ──────────────────────────────────────────────────────────
initTabs();
loadDomains();
refreshActiveJobs();
refreshCompletedJobs();
refreshSnapshots();
refreshGuardrail();
setInterval(refreshActiveJobs, 2000);
setInterval(refreshCompletedJobs, 5000);
```

- [ ] **Step 3: Commit**

```bash
git add artmind/webui/static/dashboard.js
git commit -m "feat(admin-ui): add generic data-attribute tab controller

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Inline job → file → chunk drill-down that survives polling

Replace the flat `<ul class="file-list">` in active job cards with expandable `<details>` rows that render the existing chunk grid on demand. `refreshActiveJobs` wipes its container every 2s, so expansion state lives in a module-level `Set` and is restored on each rebuild. A `quiet` flag stops the 2s refresh from flashing "Loading chunks…" over an already-rendered grid.

**Files:**
- Modify: `artmind/webui/static/dashboard.js` — `refreshActiveJobs` (lines 114-141) and `showChunkGrid` (line 167)

- [ ] **Step 1: Add the `quiet` parameter to `showChunkGrid`**

Change the signature at line 167 and its first statement. Replace:

```javascript
async function showChunkGrid(container, jobId, domain, docName) {
  container.innerHTML = "Loading chunks…";
```

with:

```javascript
async function showChunkGrid(container, jobId, domain, docName, quiet = false) {
  if (!quiet) container.innerHTML = "Loading chunks…";
```

Everything else in the function is unchanged — it already rebuilds `container` from scratch once the fetch resolves, and the drawer path (`showJobDetail`) keeps its loading text because it does not pass `quiet`.

- [ ] **Step 2: Replace `refreshActiveJobs`**

Replace the whole `refreshActiveJobs` function (lines 114-141, i.e. from `async function refreshActiveJobs() {` through its closing `}`) with:

```javascript
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
          // quiet when the grid is already on screen, so the poll doesn't flash
          showChunkGrid(drill, job.jobId, job.domain, docName, drill.hasChildNodes());
        } else {
          expandedFiles.delete(key);
        }
      });

      // Restore prior expansion; setting .open fires the toggle handler above.
      if (expandedFiles.has(key)) row.open = true;

      fileRows.appendChild(row);
    }
    card.appendChild(fileRows);
    container.appendChild(card);
  }
}
```

Note: expanded rows re-fetch their chunk grid on every 2s poll. That is intentional for *active* jobs — the grid live-updates as extraction progresses — and `quiet` keeps it flicker-free.

- [ ] **Step 3: Reseed and verify in the browser**

Check Neo4j is up (`nc -z localhost 7687`; ask the user if not). Then:

```bash
just dev-stop-daemons
just dev-install
```

Start the admin UI, open `/dashboard`, and start an ingest so a job is active. Verify:
1. Each file appears as a collapsed row with a status dot.
2. Clicking one expands it and shows the chunk grid (entities/properties/relationships pips) plus "Resume extraction".
3. **The row stays open across several 2s polls** and does not flash "Loading chunks…".
4. Collapsing it keeps it collapsed across polls.
5. The Active tab label shows a live count.

- [ ] **Step 4: Commit**

```bash
git add artmind/webui/static/dashboard.js
git commit -m "feat(admin-ui): inline job/file/chunk drill-down with poll-safe expansion

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: Send `stageOnly` from the Ingest form

Wire the checkbox added in Task 2 to the endpoint opened in Task 1.

**Files:**
- Modify: `artmind/webui/static/dashboard.js` — the `ingest-form` submit handler (lines 80-96)

- [ ] **Step 1: Include the checkbox in the request body**

In the `ingest-form` submit handler, replace the body-building lines. Change:

```javascript
    await api("/api/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, path }),
    });
    document.getElementById("ingest-path").value = "";
```

to:

```javascript
    const stageOnly = document.getElementById("ingest-stage-only").checked;
    await api("/api/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain, path, stageOnly }),
    });
    document.getElementById("ingest-path").value = "";
```

- [ ] **Step 2: Verify end-to-end**

With Neo4j up and the UI reseeded (`just dev-install`), tick **Stage only**, ingest a small file, and confirm:
1. A job runs and completes in the Jobs panel.
2. The document appears in **Browse** with a grey **staged** badge and a "Write to graph" button (i.e. it was *not* auto-committed).
3. Clicking "Write to graph" flips the badge to green **in graph** and the button disappears.
4. Repeating without the checkbox produces a document that is **in graph** immediately.

- [ ] **Step 3: Commit**

```bash
git add artmind/webui/static/dashboard.js
git commit -m "feat(admin-ui): send stageOnly from the ingest form

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Backend suite green:** `just dev-test` — all tests pass, including the two new ingest-payload tests.
- [ ] **No lost ids:** re-run the id-contract script from Task 2 Step 2. Expected: `MISSING: none`.
- [ ] **No console errors:** reload `/dashboard` with devtools open. The console must be clean — a `TypeError: Cannot read properties of null` means an id was dropped in Task 2.
- [ ] **Every shipped control still works** in its new home: ingest submit, embeddings backfill, domain switch in Browse, "Write to graph" on a staged doc, Export bundle link, pull form, import form, completed-job "View" (opens the drawer) and "Retry" on a failed job, create snapshot, download/restore snapshot, upload-and-restore.
- [ ] **Both themes:** toggle light/dark. Check the maintenance zone's warn tint, tab active states, and the staged/in-graph badges are legible in both.
- [ ] **Narrow viewport:** resize below 900px. The grid must collapse to one column, the left rail must stop being sticky, and the completed-jobs and snapshots tables must scroll inside their own `.table-scroll` container — the page body must never scroll sideways.

---

## Follow-up (unchanged from the staging→commit plan — still not implemented)

**Bring the "Refine steps" into the Admin UI dashboard.** Refinement (`artmind/refine_pipeline.py`: merge → conflicts → consolidate, plus the `normalize-time` / `detect-supersession` backfills) remains CLI-only, deliberately excluded from commit because it is cross-document/cross-domain LLM judgment gated by a dry-run → review-proposal-file → apply workflow. This layout work creates the natural home for it: a third zone alongside the maintenance zone, or a fourth panel in `col-main`. The unsolved design problem is unchanged — the review gate (a human editing per-domain sub-proposal JSON under `data/refine/pipeline/` before applying) has no obvious UI analogue. Spec it separately, covering: rendering the propose-mode report as a reviewable diff with per-item accept/reject, how editing proposals maps to controls, whether refine should be a tracked/queued long-running operation with progress like ingest jobs, domain scope selection, and reusing the existing snapshot guardrail pattern for its destructive steps.
