# artmind admin UI — spec

Status: **spec** (profile refactor foundation already landed — see "Step 0").
Audience: the implementing agent (targeted at Sonnet, medium effort).

## Goal

Replace the out-of-date Textual **wizard** with a browser-based **admin UI**,
built in the same style and on the same infrastructure as the existing chat
web-UI. The admin UI is a *separate* front-end surface from the end-user Q&A
chat UI, but shares its backend/session/SSE machinery.

The admin UI has **two lanes**, chosen by the nature of each operation:

- **Lane A — Agent console (intelligent).** A chat surface (identical rendering
  to the Q&A UI) scoped to graph-*maintenance* skills. For operations where the
  LLM's judgment is the value: refine, update, supersede, reconcile timelines,
  detect/resolve conflicts, consolidate descriptions, author schemas. Runs
  through **both** backends (`claude-sdk` and `acp`) via the `ADMIN_PROFILE`.
- **Lane B — Ingest dashboard (deterministic).** Plain JSON endpoints + widgets
  for ingestion and job operations. No LLM in the loop — these are database
  facts and pipeline state: drop/sync files, watch per-file job progress, retry
  failures, view results, read domain/graph stats. This replaces the Textual
  `ingest dashboard` too.

## Non-goals

- No change to the end-user Q&A chat UI's behaviour or persona.
- Lane B (the deterministic widgets) does **not** route ingestion through an
  agent — routine/bulk ingestion + job monitoring are buttons and progress
  bars, and extraction's own LLM intelligence lives inside the worker pipeline.
  The admin *agent* (Lane A) still carries `artmind-ingestion-helper` for
  guidance/troubleshooting when an operator asks — the two are complementary.
- Not exposed beyond localhost (same trust model as the chat UI:
  `bypassPermissions` / auto-allowed ACP permission requests).

---

## Step 0 — DONE: the agent-profile seam

Landed already (do not redo; build on it):

- `artmind/webui/profiles.py` — new. `AgentProfile` dataclass + `QA_PROFILE`
  (end-user: `query` + `update` only — ask questions and contribute/correct
  facts) and `ADMIN_PROFILE` (operator: `query` + `update` + `refine` +
  `create-schema` + `ingestion-helper`; acp_mode `"artmind-admin"`). `PROFILES`
  registry. **Scope boundary:** graph maintenance, schema authoring, and
  ingestion are operator-only — never in QA.
- `agent.py` — `agent_options(profile=QA_PROFILE)`; persona text moved to
  `profiles.py`.
- `backends/claude_sdk.py` — `ClaudeSDKBackend(profile=QA_PROFILE)`.
- `backends/acp.py` — new `preamble_text` param; no longer imports the persona
  from `agent.py`.
- `backends/__init__.py` — `create_backend(name, profile=QA_PROFILE)`, new
  `backend_factory(profile)` helper, re-exports the profile symbols.
- `test/test_webui_profiles.py` — covers the seam.

The seam that makes the admin app trivial:

```python
from artmind.webui.app import create_app          # unchanged, profile-agnostic
from artmind.webui.sessions import SessionRegistry
from artmind.webui.backends import backend_factory, ADMIN_PROFILE

admin_app = create_app(SessionRegistry(client_factory=backend_factory(ADMIN_PROFILE)))
```

---

## Lane A — Agent console

### Reuse verbatim
- `webui/backends/*` (transport), `webui/sessions.py` (`SessionRegistry`),
  `webui/agent.py` (`EventMapper`), the `UIEvent` contract in `backends/base.py`.
- The SSE `/api/chat` handler + `app.js` event renderer.

### New
1. **Admin FastAPI app.** Prefer a factory parameter over a second `create_app`:
   the current `create_app(registry=None)` already accepts an injected registry,
   so the admin app is *the same `create_app`* with an admin-profile registry and
   an admin `index.html`. To vary the template, add a small `template_name` (or
   `page_title`) parameter to `create_app` and pass `"admin.html"`.
2. **Admin persona for ACP.** `ADMIN_PROFILE.acp_mode == "artmind-admin"`. Add
   `artmind/opencode/agent/artmind-admin.md` (mirror the existing
   `artmind.md`, but the maintenance persona) so the opencode ACP agent knows
   the mode. `init` seeds `.opencode/` into the run folder (see CLAUDE.md), so
   after adding the file run `artmind init`. Note: ACP mode selection downgrades
   to a warning if the mode is unknown, so this is not a hard dependency — the
   `claude-sdk` backend needs no such file.
3. **Admin template + static.** `webui/templates/admin.html` and any admin
   CSS/JS. Reuse `static/style.css`, `static/app.js`, and the vendored
   `marked`/`purify`. The agent console pane should be visually identical to the
   Q&A chat; only chrome (title, nav to Lane B) differs.
4. **CLI command.** `artmind admin-ui` mirroring `chat-ui` (see `cli.py:229`):
   `--host`, `--port` (default **8379**, since chat-ui is 8378), `--acp-cmd`
   (calls `set_acp_agent_cmd`). Add a `justfile` recipe.

### Backend parity requirement (the user's ask: "work it through both")
The admin console must offer the same backend picker as the Q&A UI
(`claude-sdk` and `acp`). This is already satisfied by `ChatRequest.backend` +
`backend_factory` — no per-backend admin code. Verify manually against both.

---

## Lane B — Ingest dashboard (deterministic widgets)

### Data layer already exists — wrap, don't rewrite
- `artmind/jobs.py`: `_list_jobs(status_filter)`, `_get_job_status(job_id)`,
  `_get_job_results(job_id)`, `_retry_job(job_id, include_skipped)`. All return
  plain dicts / raise `ValueError`.
- `artmind/dashboard.py`: `_fetch_active_jobs()`, `_fetch_completed_jobs(limit)`
  — already plain SQLite→dict; the web dashboard reuses these queries (the
  Textual widget code around them is discarded).
- Ingest triggers: `ingest sync` / `ingest async` (see `cli.py:402/456`) and
  `_ensure_worker_running()` for the async worker.
- Stats/reporting: `query graph metadata`, `query graph structural-metadata`,
  `query graph entity-listing`, `query domains-overview`, `domains list`,
  `ingest embed-entities`.

### New JSON endpoints (all `GET` unless noted; JSON in/out, no SSE)
| Endpoint | Wraps | Notes |
|---|---|---|
| `GET /api/jobs?status=` | `_list_jobs` | job table |
| `GET /api/jobs/active` | `_fetch_active_jobs` | live progress feed (poll ~2s) |
| `GET /api/jobs/completed?limit=` | `_fetch_completed_jobs` | history table |
| `GET /api/jobs/{id}` | `_get_job_status` | per-file progress; 404 on None |
| `GET /api/jobs/{id}/results` | `_get_job_results` | per-file results; 404 on None |
| `POST /api/jobs/{id}/retry` | `_retry_job` (+`_ensure_worker_running`) | body `{includeSkipped}`; 400 on ValueError |
| `POST /api/ingest` | `ingest async` path | body `{domain, path}`; enqueue + start worker |
| `GET /api/domains` | `domains list` | domain picker |
| `GET /api/stats?domain=` | graph metadata/overview | read-only stat cards |
| `POST /api/embed-entities` | `embed_entities_backfill(domain)` | button + result |

Import the Python functions directly (in-process) rather than shelling out to
the CLI — the admin app runs on an ingestion host with the package importable.
Keep JSON keys `camelCase` on the wire to match existing UI conventions; map to
the dicts the functions return.

### Widgets (Lane B page)
- **Domain picker + ingest form**: choose domain, choose a path/folder, submit →
  `POST /api/ingest`, then show the new job in the active feed.
- **Active jobs**: cards with per-file `current_step` and a progress bar
  (`processed_count`/`file_count`), polled from `/api/jobs/active`.
- **Completed jobs**: table (domain, files, status, timing) from
  `/api/jobs/completed`; row → detail drawer via `/api/jobs/{id}/results`.
- **Retry**: button on failed jobs → `POST /api/jobs/{id}/retry`
  (with an "include skipped duplicates" checkbox).
- **Stat cards**: entity/relationship counts per domain from `/api/stats`.
- **Embeddings backfill**: per-domain button → `/api/embed-entities`.

Style to match the Q&A UI (`static/style.css`). Prefer vanilla JS + `fetch`
(consistent with the existing `app.js`); no new frontend framework.

### Restart, portability & snapshots (Lane B, deterministic)

Three operator concepts that are CLI-only today. Each maps to an **existing**
mechanism — the new code is widgets + thin file-plumbing endpoints, not new
pipeline logic.

**Chunk-level restart.** `kg_chunk_status` tracks every chunk × phase
(`entities_status` / `properties_status` / `relationships_status`, each
`pending`/`ok`/`failed`). `_get_chunk_progress(doc_sha256)` (jobs.py) already
summarises it, and `_get_job_status` surfaces it as `chunk_progress` during the
`extract_kg` step. `extract-kg DOC --domain` resumes and **skips already-ok
chunks** (so it redoes only failures); `retry-job` is the coarser file/registry
reset.

**KG-file portability.** The portable unit is `KG_DIR/<domain>/<doc_stem>/`
(`document.json` + entity/property/relationship JSON). Import already exists:
`write-to-graph` (single or `--folder` batch) and `pull-kg` (git sparse
checkout). Only bundling (zip) + upload are new.

**Snapshots (the `session` CLI group).** `session close` = `export_graph()` →
`.tar.gz` in `data/graph_snapshot/`; `session initiate --snapshot` = wipe Neo4j
+ `import_graph()`. Round-trip exists; only list/download/upload are new.
**Surface this as "Snapshots" in the UI — never "sessions".** "session" is
overloaded three ways (graph snapshot · `update_sessions` draft workflow ·
`SessionRegistry` browser sessions); the UI must not add to that confusion.

New endpoints:

| Endpoint | Wraps | Notes |
|---|---|---|
| `GET /api/jobs/{id}/chunks?doc=` | `_get_chunk_progress` | chunk × phase grid |
| `POST /api/documents/{doc}/resume-extract` | `extract_kg` (skips ok chunks) | body `{domain}` |
| `GET /api/artifacts?domain=` | list `KG_DIR/<domain>/*` | per-doc folders + counts + in-graph flag |
| `GET /api/artifacts/{domain}/{doc}/bundle` | zip the KG folder | download `.zip` (**new**) |
| `POST /api/artifacts/import` | unzip into `KG_DIR` → `write_to_graph(folder)` | multipart upload (**new**) |
| `POST /api/artifacts/pull` | `pull_kg_fn(repo, repo_path, domain)` | git pull form |
| `GET /api/snapshots` | list `data/graph_snapshot/*.tar.gz` | name, size, date |
| `POST /api/snapshots` | `export_graph()` | create (= session close) |
| `GET /api/snapshots/{name}` | serve the `.tar.gz` | download |
| `POST /api/snapshots/import` | save upload → `import_graph(path)` | upload + restore (**new**) — **WIPES Neo4j** |
| `POST /api/snapshots/{name}/restore` | `import_graph(path)` | restore existing — **WIPES Neo4j** |

Widgets:
- **Chunk grid** (in the job-detail drawer): rows = chunks, 3 status pips
  (entities/properties/relationships). "Resume extraction" → resume-extract;
  "Retry job" (existing) for the file/registry reset.
- **Knowledge artifacts panel**: per-domain document cards (entity/prop/rel
  counts, in-graph indicator); Export bundle (zip), Import bundle (upload),
  Pull from repo (pull-kg form).
- **Snapshots panel**: list (name/size/date); Create, Download, Restore /
  Upload-and-restore.

**Destructive-op rule:** `restore` and `import` (snapshot) delete all Neo4j
data — require an explicit `{confirm: true}` in the request body (the CLI
equivalents prompt / take `--yes`) and a hard confirm in the UI.

---

## Help & guidance (drift-proof — generated, never hand-maintained)

The wizard drifted because it hand-maintained a description of the CLI. **Do not
repeat that.** All admin help derives from two existing sources of truth:

- **Skill descriptions** — `artmind/skills/<name>/SKILL.md` front-matter
  (`name` + `description`), seeded into the run folder at `.claude/skills/`, for
  the maintenance concepts (refine, update/supersede, create-schema, query,
  ingestion-helper).
- **CLI docstrings** — command help via `artmind … --help` /
  `just artmind-cli-help` for the deterministic operations.

Three layers:

1. **Active help (primary, Lane A).** Suggested-action chips in the agent
   console — e.g. "Find & resolve conflicts", "Supersede an outdated fact",
   "Merge duplicate entities", "Reconcile a timeline". A chip pre-fills the
   console; the admin agent (already loaded with the maintenance skills)
   explains and walks the operator through, using the *same* skills that do the
   work — the help is the intelligence. Seed chip labels/prompts from the skill
   descriptions.
2. **Concept drawer (reference).** A "What is this?" drawer: refine · supersede
   · conflicts · consolidate · timeline · chunk-resume · KG bundle · snapshot,
   each as *what it does · when to use · what it touches (destructive?)*.
   Generate from the skill front-matter + command docstrings at build/runtime.
3. **Inline guardrails.** A `?`/callout on every destructive control (snapshot
   restore = wipe, supersession, refine merges) explaining the consequence
   before the click — the UI equivalent of the CLI's confirmations.

**Implementation:** a single `GET /api/help/concepts` endpoint reads the
`SKILL.md` front-matter from the run folder's `.claude/skills/` plus selected
command docstrings and returns the concept list as JSON — one generator, zero
hand-maintained catalogue. If skills/CLI change, the help updates for free.

---

## Retirement (do last, after Lane A + B verified) ✅ DONE
- Delete `artmind/wizard.py`, `artmind/wizard_commands.py`, the `wizard` CLI
  command (`cli.py:1704`), its import (`cli.py:20`), and the `justfile` recipe.
- Remove `wizard` from the setup/help metadata (`cli.py:159`).
- After Lane B replaces it: delete `artmind/dashboard.py` and the
  `ingest dashboard` command (`cli.py:546`), keeping the `_fetch_*` query logic
  by moving it into the admin app (or a shared `jobs.py` helper) first.
- Drop `WIZARD_FIXTURES_DIR` / wizard-only bits from `paths.py` if now unused.
- Grep for `wizard` across `docs/`, `justfile`, skills, and update.

---

## Cross-cutting cleanups (small, tracked in the plan)
- **`clip` decoupling.** `backends/acp_events.py` imports `clip` from the
  SDK-coupled `agent.py`, so importing the ACP backend still pulls in
  `claude_agent_sdk`. Move `clip`/`TRACE_CLIP` to a dependency-free module
  (e.g. extend `backends/base.py` or a new `webui/_util.py`) and import it from
  both `agent.py` and `acp_events.py`. Makes the ACP path truly SDK-free
  (matters for ACP-only hosts).
- **`RUN_FOLDER`** is defined in `agent.py`; `create_backend('acp')` imports it
  from there. Optional: source it from `paths.ARTMIND_HOME` directly to avoid
  the agent.py import on the ACP path.

## Acceptance
- Q&A UI unchanged (persona, skills, both backends) — existing web-UI tests green.
- Admin UI Lane A answers a maintenance request end-to-end on **both** backends.
- Admin UI Lane B: ingest a doc, watch it progress, see it complete, retry a
  forced failure — all via widgets, no agent.
- **Chunk restart:** a doc with a failed chunk shows the chunk grid; "Resume
  extraction" clears only the failed cells.
- **KG portability:** export a document's KG bundle on one install, import the
  zip on another, and it writes to that Neo4j.
- **Snapshots:** create a snapshot, download it, restore it (behind the wipe
  confirm) and the graph comes back.
- **Help:** the concept drawer and chips render from skill/CLI descriptions;
  editing a `SKILL.md` description changes the help with no UI code change.
- Wizard + Textual dashboard removed; `just test` green; `just artmind-cli-help`
  shows no `wizard`.
