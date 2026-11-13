# Curation surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Curate tab in the admin console for every review queue, plus Curate-row links and note commands in the Obsidian plugin (spec `docs/superpowers/specs/2026-11-12-curation-surfaces-design.md`).

**Architecture:** `artmind/webui/curate_routes.py` (`register_curate_routes(app)`, admin profile only) calls the same library functions the CLI calls (`sameas.*`, `conflicts.resolve_conflict`, ...), Neo4j work off the event loop with `asyncio.to_thread`. Page code in `static/curate.js` (textContent only), tab in `templates/dashboard.html`. Every write route takes `dry_run` and returns the library's `effect`. Branch: `curation-surfaces`. Each build step below ships on its own.

**Tech Stack:** FastAPI, pytest `TestClient`, vanilla JS, TypeScript + vitest (plugin).

---

## File structure

| File | Responsibility |
|---|---|
| `artmind/webui/curate_routes.py` | routes `/api/curate/*`; pure helpers `classify_proposal`, `parse_member`, `domain_matches` |
| `artmind/webui/static/curate.js` | tab UI: overview strip, queue panes, deep link `?tab=curate&queue=` |
| `artmind/webui/templates/dashboard.html` | tenth tab + panel, script tag |
| `artmind/webui/static/dashboard.js` | honour `?tab=` on load |
| `artmind/webui/app.py` | register routes in the admin block |
| `test/test_curate_routes.py` | route tests (mocked library calls; assert parameters sent) |
| `obsidian/artmind-obsidian/src/{checklist,controller,main,texts}.ts` | Curate-row links (step 1), note commands (step 3) |

## Step 1 — routes, same-as pane, overview, plugin row links

### Task 1: pure helpers + same-as read routes
- [ ] Write `test/test_curate_routes.py`: `classify_proposal` returns `link` when members differ in class or domain, `merge` otherwise; `domain_matches("banking.a", "banking")` true, `("bankingx","banking")` false; `GET /api/curate/sameas` (monkeypatched `sameas.list_proposals`) returns rows with parsed members, `action`, and filters by domain/source.
- [ ] Run `uv run --group dev pytest test/test_curate_routes.py -q` → FAIL (module missing).
- [ ] Implement helpers and `GET /api/curate/sameas`, `GET /api/curate/sameas/{id}/evidence` (per member: `observations.entity_id(key)` → `conflicts.gather_evidence(session, id, 3)`).
- [ ] Run tests → PASS. Commit.

### Task 2: same-as write routes
- [ ] Tests: `approve` with `dry_run: true` calls `sameas.approve_many(ids, canonical=None, dry_run=True)` and returns `effect`; with `false` calls it with `dry_run=False`; `canonical` only accepted with one id (400 otherwise); `reject` calls `sameas.reject(id, reason, dry_run=...)` per id, reason required (422/400 when empty and not dry run is fine; require always).
- [ ] Implement `POST /api/curate/sameas/approve`, `/reject`; `POST /api/curate/sweep` (`dry_run` → `projection.plan_full_rebuild(None)`; real → background thread running `ingest.rebuild_and_sweep_all`, polled by `GET /api/curate/sweep`); `GET /api/curate/overview` (same-as open count, conflicts open count via `list_conflicts`, projection drift via `projection.status`; each part fails soft into `warnings`).
- [ ] Tests pass; commit.

### Task 3: register + tab + page
- [ ] Test: admin app has `/api/curate/overview`, chat-only app does not (see how `test_insights_routes`/app tests build the app).
- [ ] `app.py`: `register_curate_routes(app)` after `register_topics_routes`. `dashboard.html`: tab button `data-tab="curate"`, panel with `#curate-*` ids, `<script src="{{ static_url('curate.js') }}">`.
- [ ] `curate.js`: domain filter, overview strip, same-as pane (group by class, search, status/source filters, row open → evidence, select, Approve/Reject → dry-run effect → Confirm, canonical override on a single row, sweep offer after approvals), "Ask the agent" link (`/?ask=`).
- [ ] `dashboard.js`: on load read `?tab=` / `?queue=`, click the matching tab, dispatch `curate:queue`.
- [ ] Verify live in the browser pane against a console (dry-run only).

### Task 4: plugin Curate row links
- [ ] vitest: the Curate row's review buttons carry `/dashboard?tab=curate&queue=sameas|conflicts` via `openAdmin`; counts unchanged. Implement in `checklist.ts`/`controller.ts`; `just obsidian-plugin-test`.

## Step 2 — conflicts pane
`GET /api/curate/conflicts`, `POST /api/curate/conflicts/{id}/close` → `conflicts.resolve_conflict(id, status, reason, dry_run=)`; pane with both sides, older-version hint, required reason; tests as Task 2.

## Step 3 — plugin note commands
Supersede / unsupersede / retire / restore / archive (double confirm) / history; each builds the CLI argument list, shows `--dryRun` effect before running; vitest for argument lists and the double confirmation.

## Step 4 — tables pane
`GET /api/curate/tables`, mapping/grain/bridge/catalogue routes over the `db` library functions (move Click-body logic into shared functions; CLI output unchanged, plugin fixtures regenerated only if they change); plugin table-review link.

## Step 5 — graph-health and mart panes
`GET /api/curate/health`, `GET /api/curate/mart`, `POST /api/curate/mart/member`; actions per `graph-health.md`.

## Self-review
Spec §2.2 (list, merge/link badge, evidence, batch approve/reject with preview, canonical override, sweep), §2.1 overview, §2.7 ask-agent, §3.1 row links are covered by Step 1; §2.3–§2.6 and §3.2 by steps 2–5. Types: `classify_proposal`, `parse_member`, `domain_matches` are used consistently in tasks 1–3.
