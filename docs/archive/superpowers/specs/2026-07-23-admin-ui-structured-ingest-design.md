# Admin UI: Structured Data Tab — Design Spec

**Status:** Draft — pending review
**Date:** 2026-07-23
**Owner:** Surjit Das

## 1. Purpose

The structured-data-ingestion feature (`docs/superpowers/plans/2026-07-23-structured-data-ingestion-plan.md`,
`docs/superpowers/specs/2026-07-23-structured-data-ingestion-design.md`) gave
`artmind` a CLI surface — `artmind db list/schema/sql/mappings/refresh/connect`
— for a DuckDB/parquet structured store that lives alongside the knowledge
graph. The admin console (Lane B, `artmind admin-ui`) has no visibility into
any of it: an admin can't see what tables exist, inspect a schema, or run a
query without dropping to a terminal. This spec adds a **Structured data**
tab to the existing dashboard that surfaces the `db` command group visually,
following the same deterministic, no-LLM, JSON-wrapper pattern Lane B already
uses for jobs/artifacts/snapshots.

## 2. What already works today, for free

`artmind/cli.py`'s `ingest_sync` and `artmind/worker.py`'s `_process_job` both
dispatch on `is_structured_source()` before the KG path (Phase 1, already
shipped). That means the dashboard's existing generic **Ingest** panel
(`#ingest-form` → `POST /api/ingest` → `_create_job` → worker) *already*
ingests a `.csv`/`.xlsx`/`.xlsm` file into the structured store correctly —
no backend change is needed for ingestion itself to work end-to-end through
the UI today.

The gap is entirely on the **visibility** side: the existing "Browse" panel
(`#artifacts-panel`) and job-detail drawer are built around KG artifacts
(entity/property/relationship counts, "write to graph") and have nothing
meaningful to show for a parquet table. An admin who ingests a csv today sees
a job go to "completed" and then has no way to confirm what landed, inspect
its schema, or query it.

## 3. Guiding principles

Mirrors the backend spec's seams, adapted for the UI layer:

1. **The tab reflects the store's independence.** `db sql` involves the graph
   not at all — the UI's SQL runner must keep working with Neo4j down, same
   as the CLI. No panel in this tab may silently depend on a live graph
   connection except the (clearly labelled) catalogue/mapping panels that are
   inherently graph-writing.
2. **No new ingestion path.** Reuse the existing `#ingest-form` for
   server-side paths; this tab adds a **file-upload** ingestion option (better
   suited to a browser-based admin who may not have a path on the server) but
   both funnel into the same `ingest_structured_file()` — never a second,
   divergent code path.
3. **Read-only by default, mutation behind explicit action.** Listing tables,
   viewing schema, and running SQL are pure reads. Confirming a mapping,
   rebuilding the catalogue, or refreshing a table are explicit, individually
   triggered actions — no bulk "sync everything" button.
4. **Match Lane B's existing idioms exactly** — see §5. A developer who knows
   the current dashboard should be able to predict this tab's markup, JS, and
   route shape without reading new conventions.

## 4. UI design

### 4.1 New top-level tab

Add `Structured data` as a fourth `view` tab, alongside `Ingest & browse` /
`Benchmark` / `Maintenance` (`dashboard.html:33-36`):

```html
<button type="button" class="tab" data-tab="structured">Structured data</button>
...
<div class="tab-panel" data-tab-group="view" data-tab-panel="structured"> ... </div>
```

**Why a new top-level tab, not a section inside "Ingest & browse":** the
structured store is a first-class, independently-queryable system (backend
spec principle #1), not a KG appendage — folding it into the KG-oriented
"Ingest & browse" panel would bury it under artifact/job terminology that
doesn't apply (there's no "write to graph" step, no chunk grid, no staged-vs-
committed distinction). A sibling tab keeps the mental model clean and is
consistent with how "Benchmark" already gets its own tab rather than living
inside "Ingest & browse".

### 4.2 Panel layout (mirrors `dash-grid` / `col-side` + `col-main`)

```
┌─ Structured data ────────────────────────────────────────────────┐
│ stat-row: tables · total rows · domains covered                   │
├───────────────────┬────────────────────────────────────────────────┤
│ col-side           │ col-main                                       │
│                     │                                                │
│ Ingest structured   │ Tables  (domain selector, reused pattern)      │
│ file (upload form)  │  ┌ name │ domain │ rows │ version │ ingested ┐│
│                     │  └──────┴────────┴──────┴─────────┴──────────┘│
│ Run SQL             │  → click a row to expand:                    │
│ (textarea + button, │    - columns + dtypes                        │
│  results table)     │    - mappings  (Phase 2+, "not confirmed yet" │
│                     │      badge until reviewed)                    │
│                     │    - [Rebuild catalogue] (Phase 3+)           │
│                     │    - [Refresh] (Phase 5+, replace/temporal)   │
└───────────────────┴────────────────────────────────────────────────┘
```

- **Stat row** reuses the existing `.stat-row`/`#stat-cards` pattern (already
  populated by `refreshStats()` for the KG side) — a sibling
  `refreshStructuredStats()` populates table/row/domain counts from
  `GET /api/structured/tables`.
- **Domain selector** reuses the existing `<select id="...-domain">` +
  `loadDomains()` pattern already wired for `#ingest-domain`, `#embed-domain`,
  `#artifacts-domain`.
- **Tables list** is a `.dash-table` (same class as `#completed-table`,
  `#snapshots-table`), one row per registered table for the selected domain
  (or all domains, matching `db list`'s optional scoping).
- **Row expansion** reuses the disclosure-row idiom already used for job rows
  in `refreshActiveJobs()` (`row.addEventListener("toggle", ...)`).
- **Ingest form** is a new upload-based form (`multipart/form-data`), separate
  from `#ingest-form` per §3.2 — fields: domain (select), file (`<input
  type=file accept=".csv,.xlsx,.xlsm">`), optional `table`/`sheet`/`headerRow`
  (collapsed under "Advanced"), `force` checkbox. Mirrors `#import-kg-form`'s
  upload shape (`artmind/webui/templates/dashboard.html:151-169`).
- **SQL runner** is a `<textarea>` + "Run" button + results rendered as a
  `.dash-table` built from the returned rows' keys (first row's keys become
  headers) — no query builder, no autocomplete (see §7 Out of scope).

## 5. API surface (new routes in `dashboard_routes.py`)

All new routes follow the existing file's conventions exactly: deterministic
wrappers around `artmind.structured.*`, `_camelize()` on the way out, blocking
calls wrapped in `asyncio.to_thread` (matching `api_artifact_import`,
`api_resume_extract`), and `_validate_artifact_segment`-style guards on any
value used to build a filesystem path.

```python
GET  /api/structured/tables?domain=...          # registry.list_tables(); domain optional, comma-splittable like elsewhere
GET  /api/structured/tables/{table}/schema?domain=...   # columns + dtypes + mappings (mirrors `db schema`)
POST /api/structured/ingest                     # multipart: domain, file, table?, sheet?, headerRow?, force?
POST /api/structured/sql                         # body: {sql, domain?} -> validate_read_only_sql, then DuckDBDatasource().run_sql
```

Later increments (gated on their backend phase landing — see §6):

```python
GET  /api/structured/tables/{table}/mappings            # Phase 2
POST /api/structured/tables/{table}/mappings/confirm     # Phase 2 — body: {column, entityClass}
POST /api/structured/tables/{table}/mappings/clear       # Phase 2
POST /api/structured/catalogue/rebuild                   # Phase 3 — body: {domain}
POST /api/structured/tables/{table}/refresh               # Phase 5 — body: {domain}
```

`POST /api/structured/ingest` request/response shape (mirrors `IngestRequest`
already in the file):

```python
class StructuredIngestRequest(BaseModel):
    domain: str
    table: str | None = None
    sheet: str | None = None
    header_row: int = Field(0, alias="headerRow")
    force: bool = False
    model_config = {"populate_by_name": True}
```

Handler saves the `UploadFile` to a `tempfile.NamedTemporaryFile` (suffix
preserved from the original filename so `is_structured_source`/DuckDB's
extension sniffing still works), calls `ingest_structured_file` via
`asyncio.to_thread`, deletes the temp file in a `finally` — same shape as
`api_artifact_import`'s zip-upload handling, minus the zip-specific
path-traversal checks (not applicable — DuckDB writes to a path this code
computes itself, never one derived from the upload).

`POST /api/structured/sql` returns the same envelope shape as the CLI's
`db sql` (`{"query_type": "sql", "command": "db sql", "rows": [...]}`) so a
future thin client (or a curl-based smoke test) gets an identical contract
whether it goes through the CLI or the dashboard.

## 6. Phased rollout (mirrors the backend plan's phases)

| UI increment | Needs backend phase | Ships |
|---|---|---|
| Tables list, schema view, SQL runner, upload-ingest | Phase 1 (shipped) | Now — no backend blocker |
| Mappings review (proposed/confirm/clear badges) | Phase 2 | After `db mappings` lands |
| "Rebuild catalogue" button + a note on what's now visible in the graph | Phase 3 | After `db catalogue` lands |
| — (no dedicated UI; `query text2sql`/`resolve-key` are agent-console concerns, not dashboard ones) | Phase 4 | N/A — out of scope for this tab, see §7 |
| Per-table "Refresh" action, `_current`/`--asOf` note for temporal tables | Phase 5 | After `db refresh` + SCD-2 land |
| — | Phase 6 | N/A — skill-level routing, not a dashboard concern |

The first row is buildable immediately against the code already on
`feature/structured-data-ingestion`; the rest are additive and don't block
each other or the first row.

## 7. Out of scope

- **NL→SQL in the dashboard.** `query text2sql` is a Q&A/agent-console
  concern (Lane A), not a Lane B deterministic-JSON concern — this tab's SQL
  runner takes literal SQL only, same as `db sql`.
- **Messy-spreadsheet cleaning UI.** A sheet with no clean header row already
  errors out with guidance to use the `xlsx` skill (backend spec §6.1) — this
  tab surfaces that error message, it doesn't try to fix the file.
- **Visual schema/ER designer, drag-drop CSV preview, query autocomplete.**
  None of the rest of Lane B has this kind of authoring surface; adding it
  here would be inconsistent with the dashboard's "thin JSON client" design.
- **External datasource connections** (`db connect`) — stubbed in the backend
  for v1; no UI surface until an adapter actually exists.
- **Bulk actions** ("rebuild every table's catalogue", "refresh everything") —
  per principle #3, mutation stays one explicit action at a time.

## 8. Testing implications

Follows the existing Lane B convention exactly (`test/test_webui_admin_api.py`):
`FastAPI TestClient` against `create_app(admin_routes=True)`, with
`artmind.structured.*` functions monkeypatched on `dashboard_routes` (not a
real DuckDB/registry) — hermetic, no Neo4j, no network, consistent with
`just dev-test`'s existing guarantees. The upload-ingest route needs one test
using `TestClient`'s multipart file support (mirrors
`test_webui_admin_api.py`'s existing zip-upload test for `/api/artifacts/import`).
A live end-to-end check (real DuckDB, real csv) needs `artmind admin-ui`
running and a browser — same caveat as the rest of the dashboard, and the
same `artmind init` reseed rule applies if this change touches any skill.

## 9. Suggested next step

This is a design spec, not an implementation plan. If you want to build it,
run `/writing-plans` against this file to produce a TDD task breakdown (one
task per route + one per UI panel, each with a failing-test-first step),
the same way the backend spec turned into
`docs/superpowers/plans/2026-07-23-structured-data-ingestion-plan.md`.
