# artmind admin UI — implementation plan

Companion to [admin-ui-spec.md](admin-ui-spec.md). Tasks are ordered and
self-contained, sized for **Sonnet (medium effort)**: each names the files to
touch, the reuse points, and a check to run before moving on. Run `just test`
after every task; keep it green.

**Test reality (from CLAUDE.md):** unit tests are hermetic (`CliRunner`, mocks,
no Neo4j, no network) and bypass the `_entry` proxy. So per-task checks are unit
tests; Lane-A and Lane-B *behavioural* verification needs a live Neo4j and a
real invocation with `ARTMIND_NO_PROXY=1` (or a restarted daemon), plus
`artmind init` to reseed the run folder after skill/opencode edits.

---

## Phase 0 — Foundation ✅ DONE
Agent-profile seam (`profiles.py`, parameterized `agent_options` / backends /
`create_backend` / `backend_factory`, `test_webui_profiles.py`). Scope:
**QA = `query` + `update`** (ask + contribute facts); **ADMIN = `query` +
`update` + `refine` + `create-schema` + `ingestion-helper`** (full maintenance;
ingestion-helper is admin-only). `just test` green.

---

## Phase 1 — Admin app shell + Lane A (agent console) ✅ DONE

### Task 1.1 — Parameterize `create_app` for a template/title
- **Files:** `webui/app.py`.
- Add a `template_name: str = "index.html"` (and optional `page_title`) param to
  `create_app`; the `/` route renders it. Default keeps the Q&A UI identical.
- **Check:** existing `test_webui_app.py` still green (default unchanged).

### Task 1.2 — Admin app entry + CLI command
- **Files:** `webui/app.py` (add `run_admin_ui(host, port)` mirroring
  `run_chat_ui`, wiring `create_app(SessionRegistry(client_factory=
  backend_factory(ADMIN_PROFILE)), template_name="admin.html")`); `cli.py`
  (add `admin-ui` command mirroring `chat-ui` at `cli.py:229`, default port
  **8379**, `--acp-cmd` → `set_acp_agent_cmd`); `justfile` (recipe).
- **Check:** `artmind admin-ui --help` works; add a CLI test asserting the
  command exists and its options (mirror an existing `chat-ui` test if present).

### Task 1.3 — Admin template + chrome
- **Files:** `webui/templates/admin.html` (clone `index.html`, retitle, add a
  nav link to Lane B `/dashboard`); reuse `static/style.css`, `static/app.js`,
  vendored `marked`/`purify`. Add admin-only CSS/JS only if needed.
- **Check:** manual — `artmind admin-ui`, open `:8379`, agent console renders;
  send a prompt on **claude-sdk** and confirm streaming works.

### Task 1.4 — ACP admin persona
- **Files:** `artmind/opencode/agent/artmind-admin.md` (mirror
  `artmind/opencode/agent/artmind.md`, maintenance persona matching
  `ADMIN_SYSTEM_APPEND`). Run `artmind init` to seed `.opencode/` into the run
  folder.
- **Check:** manual — switch the admin console to the **acp** backend, confirm
  it connects and the `artmind-admin` mode is selected (no warning in logs).
  Both backends now work through the admin console (the user's core ask).

---

## Phase 2 — Lane B (ingest dashboard, deterministic) ✅ DONE

### Task 2.1 — Share the dashboard query layer
- **Files:** `artmind/dashboard.py` → move `_fetch_active_jobs` /
  `_fetch_completed_jobs` into `artmind/jobs.py` (or a new `jobs_query.py`) so
  both the (soon-removed) Textual dashboard and the web endpoints import them
  from one place. Leave the Textual UI importing the moved functions for now.
- **Check:** `just test` green; existing job tests still pass.

### Task 2.2 — Lane B JSON endpoints
- **Files:** `webui/app.py` (or a new `webui/dashboard_routes.py` mounted on the
  admin app). Implement the table in the spec: `/api/jobs`, `/api/jobs/active`,
  `/api/jobs/completed`, `/api/jobs/{id}`, `/api/jobs/{id}/results`,
  `/api/jobs/{id}/retry` (POST), `/api/ingest` (POST), `/api/domains`,
  `/api/stats`, `/api/embed-entities` (POST). Import the `jobs.py` /
  ingest / query functions **in-process**; map dicts to `camelCase` JSON;
  404 on `None`, 400 on `ValueError`.
- **Mount only on the admin app**, not the Q&A app.
- **Check:** new `test/test_webui_admin_api.py` — drive each endpoint via
  `TestClient` with the underlying `jobs.py`/query functions monkeypatched
  (hermetic, mirrors `test_webui_app.py`). Assert status codes, 404/400 paths,
  and JSON shape.

### Task 2.3 — Lane B page + widgets
- **Files:** `webui/templates/dashboard.html`, `webui/static/dashboard.js`,
  dashboard CSS in `static/style.css`. Route `/dashboard` on the admin app.
- Widgets (spec §Lane B): domain picker + ingest form; active-jobs cards with
  per-file `current_step` + progress bar (poll `/api/jobs/active` ~2s);
  completed-jobs table + detail drawer; retry button (+ include-skipped);
  stat cards; embeddings-backfill button. Vanilla JS + `fetch`.
- **Check:** manual against live Neo4j + worker — ingest a small doc, watch it
  progress to complete, open results, force a failure and retry.

### Task 2.4 — Chunk-level restart
- **Reuse:** `_get_chunk_progress` (jobs.py), `extract_kg` (skips ok chunks).
- **Files:** endpoints `GET /api/jobs/{id}/chunks?doc=` and
  `POST /api/documents/{doc}/resume-extract` (body `{domain}`) in the admin
  routes; a **chunk grid** in the job-detail drawer (rows = chunks; 3 status
  pips: entities/properties/relationships) + a "Resume extraction" button.
- **Check:** unit — endpoint returns the `chunk_progress` shape (monkeypatched);
  manual — a doc with a forced failed chunk shows red cells, and resume clears
  only those.

### Task 2.5 — KG-file portability (artifacts panel)
- **Reuse:** `KG_DIR/<domain>/<doc>/`, `write_to_graph` (folder mode),
  `pull_kg_fn`.
- **Files:** endpoints `GET /api/artifacts?domain=`,
  `GET /api/artifacts/{domain}/{doc}/bundle` (zip download — **new**),
  `POST /api/artifacts/import` (multipart zip → unzip into `KG_DIR` →
  `write_to_graph(folder)` — **new**), `POST /api/artifacts/pull` (pull-kg
  form). A **knowledge-artifacts panel**: per-domain doc cards (entity/prop/rel
  counts, in-graph flag), Export bundle, Import bundle, Pull from repo.
- **Check:** unit — list/zip/import endpoints against a temp `KG_DIR`;
  manual — export a doc bundle, import the zip on a second install, confirm it
  writes to that Neo4j.

### Task 2.6 — Snapshots panel (graph portability)
- **Reuse:** `export_graph()` (session close), `import_graph(path)` (session
  initiate). Snapshots live in `data/graph_snapshot/*.tar.gz`.
- **Files:** endpoints `GET /api/snapshots`, `POST /api/snapshots` (create),
  `GET /api/snapshots/{name}` (download), `POST /api/snapshots/{name}/restore`
  and `POST /api/snapshots/import` (upload+restore — **new**). Restore/import
  **wipe Neo4j** → require `{confirm: true}` in the body. A **Snapshots panel**
  (name/size/date; Create, Download, Restore, Upload-and-restore) with a hard
  confirm on restore.
- **UI naming:** label this "Snapshots", never "sessions" (see spec — the term
  is overloaded three ways).
- **Check:** unit — list/create/restore with `import_graph`/`export_graph`
  monkeypatched; assert restore without `confirm` is rejected. Manual — create,
  download, restore round-trip.

---

## Phase 3 — Help & guidance (drift-proof) ✅ DONE

### Task 3.1 — Concept catalogue generator
- **Principle:** generate from sources of truth, never hand-maintain (this is
  what sank the wizard).
- **Files:** a small module (e.g. `webui/help.py`) that reads `SKILL.md`
  front-matter (`name` + `description`) from the run folder's `.claude/skills/`
  and selected CLI command docstrings, returning a concept list; expose as
  `GET /api/help/concepts`.
- **Check:** unit — the generator picks up a skill's `description` verbatim;
  editing the description changes the output with no other code change.

### Task 3.2 — Help surfaces in the UI
- **Files:** admin templates/JS. Three layers (spec §Help):
  (1) **suggested-action chips** in the agent console that pre-fill a prompt
  (labels seeded from `/api/help/concepts`); (2) a **"What is this?" concept
  drawer** rendering `/api/help/concepts`; (3) **inline guardrail callouts** on
  destructive controls (snapshot restore, supersession, refine merges).
- **Check:** manual — a chip pre-fills and runs through the admin agent; the
  drawer lists concepts; destructive controls show the consequence before click.

---

## Phase 4 — Retire the TUIs ✅ DONE

### Task 4.1 — Remove the wizard
- **Files:** delete `artmind/wizard.py`, `artmind/wizard_commands.py`; remove the
  `wizard` command + import (`cli.py:20`, `cli.py:1704`) and the
  setup/help metadata entry (`cli.py:159`); drop the `justfile` wizard recipe;
  remove `WIZARD_FIXTURES_DIR` and other wizard-only bits from `paths.py` if
  unused; delete any wizard tests.
- **Check:** `just test` green; `just artmind-cli-help` shows no `wizard`;
  `grep -rn wizard` (excluding build artifacts) is clean.

### Task 4.2 — Remove the Textual dashboard
- **Files:** delete `artmind/dashboard.py` (query functions already moved in
  2.1); remove the `ingest dashboard` command (`cli.py:546`) and its
  `run_dashboard` import; drop the `justfile` recipe.
- **Check:** `just test` green; `just artmind-cli-help` shows no
  `ingest dashboard`.

### Task 4.3 — Docs sweep
- **Files:** `docs/`, `justfile`, `CLAUDE.md`, skills — update any mention of
  `wizard` / `ingest dashboard`; point operators at `artmind admin-ui`.

---

## Phase 5 — Cross-cutting cleanup (independent; can run anytime after Phase 0) ✅ DONE

### Task 5.1 — Make the ACP path SDK-free
- **Files:** move `clip` + `TRACE_CLIP` out of `agent.py` into a dependency-free
  module (extend `backends/base.py` or add `webui/_util.py`); import from both
  `agent.py` and `backends/acp_events.py`. Optionally source `RUN_FOLDER` from
  `paths.ARTMIND_HOME` so `create_backend('acp')` needn't import `agent.py`.
- **Check:** `python -c "import artmind.webui.backends.acp_events"` does not pull
  `claude_agent_sdk` into `sys.modules`; `just test` green.

---

## Phase 6 — Curate tab (backlog, not scheduled)

Same-as identity review, conflict resolution, and the rebuild that applies
them, as a Lane B tab. Full design, grounded in Phase 8's actual cutover
curation session (180 same-as proposals + 12 conflicts reviewed and applied
by hand via CLI + a one-off Artifact console):
[admin-ui-curation-workflow.md](admin-ui-curation-workflow.md). Includes a
real defect to fix first (`sameas.approve()`'s domain-scoping is a no-op for
a single-top-level-domain corpus, making every approval as expensive as a
full rebuild) — see that doc's "A defect this workflow should also fix."

---

## Suggested execution
- Phases are ordered; **within a phase, tasks are sequential**. Phase 5
  (cleanup) is independent and can run any time after Phase 0.
- Land each task as its own commit (green tests) so review is incremental.
- Do NOT retire the TUIs (Phase 4) until Lanes A + B (incl. restart,
  portability, snapshots) are verified against a live Neo4j — the
  wizard/dashboard are the current fallback.
