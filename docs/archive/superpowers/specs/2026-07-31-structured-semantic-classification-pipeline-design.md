# Structured semantic classification as a staged pipeline (LLM-based grain, bridge, mapping)

**Status:** Design — for review
**Date:** 2026-07-31
**Owner:** Surjit Das
**Amends:** `2026-07-23-structured-data-ingestion-design.md` §6 Phase 1 step 3 ("auto-propose
mappings … query the domain KG … match … exact then fuzzy/embedding") and §8's framing of
`db mappings` as a review gate over a single deterministic proposer.
**Extends:** `2026-07-25-cross-store-join-model-design.md`'s "`domain` is not the join, `entity_class`
is the routing key" argument one layer further: mapping proposal itself should not depend on
live graph entities either (see §3).
**Motivated by:** `docs/CAPABILITIES.md` row 5.4, whose Statement claims mapping candidates are
"LLM-proposed." They are not — `artmind/structured/mappings.py`'s `propose_mappings` is exact
+ `difflib`-fuzzy string matching against live KG entity names, zero LLM calls. This spec makes
that claim true, properly, rather than just correcting the doc.

## 1. Purpose

Today, a table's three semantic properties are produced by two different, differently-shaped
mechanisms wearing the same "propose → confirm" clothing:

| Property | Mechanism | Cadence | Depends on |
|---|---|---|---|
| `grain` (instance/lookup/normative) | One LLM call (`semantics.propose_semantics`) | First registration only | Nothing but the table itself |
| `bridge_columns` | Same LLM call | First registration only | Nothing but the table itself |
| `column → entity_class mappings` | Deterministic exact/`difflib` match | Every ingest **and** every refresh | The domain's **already-extracted graph entities** |

The mapping mechanism has three problems, not one:

1. **It's not LLM-based**, contrary to the capability map's claim (5.4) and contrary to what a
   genuinely semantic judgment needs — string overlap can't tell that a `product_name` column
   full of a bank's marketing names denotes the same thing as the graph's `PRODUCT` class unless
   the exact strings happen to already be extracted verbatim elsewhere.
2. **It has a chicken-and-egg dependency** grain/bridge don't have: a domain must already have
   ingested documents and extracted entities before its *tables* can be classified at all. A
   structured file ingested into a brand-new domain gets zero mapping proposals until someone
   separately ingests and extracts KG content for that domain.
3. **It re-runs on every write**, which is the right cadence for a cheap local string match but
   would be the wrong cadence to bolt an LLM call onto unchanged — this is exactly the tension
   that came up while scoping this spec (see §4).

The fix on the table is not "swap the matcher for an LLM call and merge it into
`propose_semantics`" — that just relocates the automatic/manual tension into a bigger call. The
actual fix is to stop treating "propose semantics" as one atomic operation and instead give it
the same shape KG extraction (`docs/CAPABILITIES.md` 3.1) already has: **independent, resumable
steps**, with a default automatic run for the common case and a standalone re-entry point for
everything else.

## 2. The KG-extraction pattern this mirrors

`ingest.py:extract_kg` does not extract a chunk's entities, properties, and relationships as one
inseparable unit. Each is its own step with its own status
(`entities_status`/`properties_status`/`relationships_status` on `kg_chunk_status`, one row per
chunk). A chunk resumes at exactly the step that failed — a relationships-step timeout doesn't
force re-running an already-`ok` entities step, and `extract-kg` is a standalone re-entry point
into the *same* code `ingest sync`/`async` already call inline, "not an alternate implementation"
(3.1's grounding note).

Applied to a table instead of a document, and to {grain, bridge, mapping} instead of {entities,
properties, relationships}, this dissolves the "automatic vs. manual" question entirely: it was
never a binary. KG ingestion answers it with *both* — an automatic default path for the common
case, plus an independently-callable, per-step-resumable re-entry point for everything else — and
structured classification should answer it the same way.

## 3. Mapping's new mechanism: class-only, schema-driven, no live KG dependency

Mapping proposal stops calling `graph_query.entity_listing()` (i.e. stops depending on the domain
already having extracted entities) and instead reads the **domain schema** — the same
self-contained artifact `docs/CAPABILITIES.md` 1.1/1.3 already treats as the single source of
truth for a domain:

```
temporal.load_schema(domain)["entity_types"]        # bare class names
temporal.load_schema(domain)["entities_prompt"]      # prose per class
        │
        ▼
schema_reference.parse_entities(entities_prompt)     # -> [{class, description, types}]  (existing parser, reused as-is)
```

`schema_reference.parse_entities` already exists and already extracts exactly this shape for the
admin-ui's Schemas tab — this spec reuses it rather than writing a second parser against the same
prose convention.

The LLM prompt for a table's mapping step therefore looks structurally like `semantics.py`'s
existing grain/bridge prompt (table name, domain, row_count, refresh_mode, per-column
`name (dtype) kind=… sample=[…]`), plus a new block: `{class, description, types}` for every
class in the domain's (harmonized) `entity_types` — **no entity names, no graph query**. The
question posed is semantic ("do this column's sampled values look like instances of this class,
based on its description?"), not lexical — which is the actual reason to use an LLM here at all;
a lexical question is what the retired `difflib` matcher already answered adequately.

Output shape, one call, all candidate classes considered together:
```json
{"column": "product_name", "entity_class": "PRODUCT", "confidence": 0.9}
```
Multiple `(column, class)` pairs per column remain legitimate (unchanged from today — a
`complaints` table's category column can still map to more than one class). The existing
`CONFIDENCE_FLOOR` gate and confirm-gate (never overwrite a `(column, entity_class)` pair a human
already confirmed) carry forward unchanged.

**Consequence worth reflecting back into `docs/CAPABILITIES.md` once built:** mapping proposal no
longer requires the domain to have any ingested documents or extracted entities at all — only its
schema file. `db propose` becomes usable immediately after a structured file lands in a domain,
closing the chicken-and-egg gap in §1.2.

## 4. The three steps, and what "status" means for each

| Step | Judges | LLM input | Unchanged confirm-gate |
|---|---|---|---|
| `grain` | instance / lookup / normative + reason | table metadata + column profiles | `grain_confirmed` |
| `bridge` | which columns' *values* are fusion search terms | table metadata + column profiles | `column_roles.confirmed` |
| `mapping` | which columns denote instances of which `entity_class` | table metadata + column profiles + **schema's `{class, description, types}`** (§3) | `column_mappings.confirmed` |

Each step needs a **run status** distinct from the confirm-gate — the confirm-gate answers "did a
human decide this," run status answers "did the LLM attempt this since the last profile change,
and did it succeed." Today neither grain nor bridge tracks this explicitly (a failed
`propose_semantics` call is only visible as a warning in the ingest log); this spec makes it a
first-class, queryable thing, mirroring `kg_chunk_status`.

**Decided (2026-07-31):** three columns directly on `tables`, not a separate audit table —
`grain_status`, `bridge_status`, `mapping_status`, each `TEXT NOT NULL DEFAULT 'pending'`
(`'pending' | 'ok' | 'failed'`). This is the *actual* precedent: `kg_chunk_status` puts three
status columns on the chunk's own row rather than normalizing into a `(chunk, step)` table, and
the rest of the codebase logs best-effort failures via loguru rather than persisting error text
(`pipeline.py`'s mapping/catalogue-projection hooks, `ingest.py`'s supersession hook) — no step
gets a persisted error message either, matching that convention. Migrated the same idempotent way
`grain`/`grain_confirmed` were added: an `ALTER TABLE "tables" ADD COLUMN ...` guarded by a
`PRAGMA table_info` check in `db._init_db()`, no new table.

`db propose`/`db bridge`/`db schema` can all surface this (`"semantics": {"grain": "ok", "bridge":
"ok", "mapping": "failed"}`) — exactly the kind of visibility `docs/CAPABILITIES.md` 2.10 already
gives KG extraction's per-chunk steps, now for tables. Diagnosing *why* a step failed means
checking logs, same as everywhere else this pattern already exists.

## 5. Trigger model

- **Physical registration** (`ingest_structured_file` / `refresh_table`'s existing column
  profiling) stays fully automatic, deterministic, no LLM — unchanged.
- **First registration only:** the pipeline automatically runs all three steps once, exactly
  mirroring today's grain/bridge cadence and now extending it to mapping. This preserves the
  "a freshly ingested table is immediately routable with zero extra steps" property — nothing
  about the day-one experience gets worse.
- **`db propose <table>`** is the standalone re-entry point, mirroring `extract-kg`'s role. Default
  behavior (no flags): re-attempt every step whose `*_status != 'ok'` — a partial failure at first
  registration is resumed automatically on the next `db propose`, no different from how a
  partially-failed chunk resumes on the next `extract-kg`. `--step grain|bridge|mapping` (repeatable)
  narrows which steps are considered at all (still skipping an already-`ok` one unless combined with
  `--redo`). `--redo` forces re-running a step even though its status is already `ok` — e.g. after a
  schema edit changes a class's description, or to get a second opinion; combine as `db propose
  <table> --step mapping --redo` to force just one step.
- **`db refresh`** does not auto-retrigger `grain` — what a table *means* is a whole-table judgment
  that doesn't change because rows arrived, so it stays manual-only on refresh, same as today.
  `bridge` and `mapping`, however, auto-run for **genuinely new columns only** when a `replace`-mode
  refresh changes the column set — comparing the column-name set before/after `replace_columns`
  identifies exactly the delta, and only those new columns get proposed (existing columns' statuses,
  confirmed or not, are untouched). This is deliberately narrow in scope: `temporal`-mode tables
  already hard-error on any column-set drift (`_validate_temporal_incoming_columns`), so this case is
  only reachable for `replace`-mode tables, and only fires on an actual schema-drift event — never on
  an ordinary same-columns refresh (the common case, which stays classification-free exactly as
  today).

This is the same two-tier shape as ingestion generally has (`ingest sync` chains
convert→chunk→extract→commit automatically for the common case; `extract-kg`/`write-to-graph`
remain independently callable for resume/retry/recovery) and refinement generally has (`refine
-pipeline` chains steps in dependency order automatically; each step's own command remains
independently callable) — not a new shape invented for this feature.

## 6. Surfaces

- **CLI:** `db propose <table> [--step grain] [--step bridge] [--step mapping] [--redo] [--model
  M] [--compact]`. Retires `--skipSemantics` (no longer meaningful once mapping is no longer a
  separate deterministic pre-pass) in favor of `--step`.
- **Admin-ui:** the structured-ingest dashboard (already sharing `ingest_structured_file`/registry
  reads with the CLI per `docs/CAPABILITIES.md` 2.2/2.3's convergence-point convention) gains a
  per-table "(re-)classify" action calling the same function, and can render `grain_status`/
  `bridge_status`/`mapping_status` per table — not a reimplementation.
- **Skill:** `artmind-ingestion-helper` gains a structured-tables section — its own description
  ("navigate ingestion stages, pick the right command, diagnose problems") already matches this job
  exactly, just not yet exercised for tables; a table stuck on `mapping_status='failed'` is the same
  shape of problem as a document stuck mid-chunk, and the skill should recognize it and walk the
  operator/agent through `db propose <table> --step mapping`. `artmind-query`'s existing content
  (which documents `db propose`/`db bridge`/`db mappings` from the *retrieval* side — how to use an
  already-classified table for fusion/routing) is left untouched; ingestion-time diagnosis doesn't
  belong there.

## 7. Testing implications

- `test_structured_semantics.py`'s existing convention — monkeypatch `call_llm`, hermetic, no
  network — extends to the mapping step's tests unchanged in spirit.
- `test_structured_mappings.py` (today's hermetic, no-mock difflib tests) is **retired**, not kept
  alongside the new mechanism — the deterministic matcher is a full replacement (per decision),
  so there is no "old path" left to keep testing. Its edge-case coverage (confidence floor,
  never-overwrite-confirmed) gets re-homed as mapping-step cases in the semantics test suite.
- New hermetic cases specific to this design: a table whose `mapping` step failed and `grain`
  succeeded resumes only `mapping` on the next `db propose`; a `--step` flag targets exactly one
  step; `--redo` re-runs a step whose status is already `ok`; a domain with no schema file present
  fails the `mapping` step clearly rather than crashing `grain`/`bridge`; a `replace`-mode refresh
  that adds one new column proposes bridge/mapping for that column only, leaving every existing
  column's status (confirmed or not) untouched; a `temporal`-mode refresh with a changed column set
  still hard-errors before any classification is attempted (unchanged `_validate_temporal_incoming_columns`
  behavior).
- `grain_status`/`bridge_status`/`mapping_status` need the same idempotent `ALTER TABLE ADD COLUMN`
  migration treatment already used for `grain`/`grain_confirmed` in `db._init_db()` — no new table,
  so no new migration *shape*, just three more guarded `ADD COLUMN` calls.

## 8. Migration

Existing `column_mappings` rows produced by the retired deterministic matcher are not
retroactively invalidated — they remain valid registry data. The next time `db propose <table>`
(or the automatic first-registration run, for a table ingested after this ships) runs the
`mapping` step, it re-proposes using the new mechanism; any pair already `confirmed` is untouched
regardless of which mechanism originally proposed it, per the unchanged confirm-gate.

## 9. Decisions (resolved 2026-07-31, after review)

1. **Status storage** — three columns on `tables` (`grain_status`/`bridge_status`/`mapping_status`),
   no persisted error text; log failures via loguru, matching the rest of the codebase's best-effort
   hooks. See §4.
2. **Force-rerun flag** — `--redo`. Avoids reusing `--force`'s existing, different meaning
   (`ingest`'s dedup override, §2.4 of the capability map) for a new concept. See §5/§6.
3. **New-column auto-retrigger on refresh** — yes, scoped to `bridge`/`mapping` for genuinely new
   columns on a `replace`-mode refresh only; `grain` stays manual-only on refresh. Narrow in
   practice: `temporal`-mode tables already hard-error on column drift, so this only ever fires on
   an actual schema-drift event. See §5.
4. **Skill placement** — `artmind-ingestion-helper`, extended with a structured-tables section;
   `artmind-query`'s existing retrieval-side content is untouched. See §6.
5. **Admin-ui widget scope** — per-table classify/redo action, plus a bulk "classify every
   unclassified table in this domain" action that loops synchronously with a lightweight,
   non-persisted progress counter — not a full async job-queue like ingestion's. See §12.

## 10. Out of scope (unchanged from the 2026-07-23/07-25 designs)

Row-level fusion; persisted `RESOLVES_AGAINST` anchor nodes; a persisted `governed_by`
relational layer (§5.2 of the cross-store design — measurement showed unscoped value-driven
search already covers this); embedding-based matching (still a documented future upgrade, now
for the *mapping* step specifically rather than a v1 gap); external SQL adapters.

## 12. Admin-ui widget: structured classification status

Extends the existing Structured data tab (`dashboard.html`/`dashboard.js`), reusing the same
building blocks the Jobs panel already established rather than inventing a new visual language —
see the comparison this section is built from:

| Jobs panel (unstructured) | This widget (structured) |
|---|---|
| `pip(status)` renders entities/properties/relationships dots in a chunk-grid | Same `pip()`, unmodified, renders grain/bridge/mapping dots |
| Expand a job → "Show chunks" → chunk-grid + "Resume extraction" button | Expand a table row (already wired) → classification block + "Classify"/"Redo" |
| Active/Completed tabs, backed by a real job queue + worker | No queue — see §9.5; a table's classification is 1–3 LLM calls, not a multi-chunk batch |
| Bulk = submit a directory as one async job | Bulk = a synchronous loop over one domain's unclassified tables (§12.3) |

**Non-goal, called out explicitly:** no confirm/set-grain controls in this widget. Today's
Structured tab already shows mapping confirmation state as read-only text ("confirmed"/"proposed")
with no in-UI way to confirm it — this widget preserves that boundary and adds only visibility +
propose/redo actions. Confirming stays a CLI/skill action (`db grain --set`, `db mappings ...
confirm`), consistent with the existing asymmetry rather than a scope expansion introduced by this
feature.

### 12.1 A gap found while reading the existing endpoint

`GET /api/structured/tables/{table}/schema` today returns `{**row, columns, mappings}` — it never
includes `column_roles` (bridge columns) at all, so the admin-ui currently has *no* visibility into
bridge columns whatsoever (CLI-only, via `db bridge`/`db grain`). This widget requires fixing that:

```python
bridge_columns = structured_registry.list_column_roles(row["id"])
return _camelize({**row, "columns": columns, "mappings": mappings, "bridge_columns": bridge_columns})
```

`grain`/`grain_confirmed`/`grain_status`/`bridge_status`/`mapping_status` need **no route change at
all** — they arrive automatically once added to `tables`, since both existing endpoints already
spread the raw registry row (`**row`) into their response.

### 12.2 Per-table classify/redo

New endpoint, mirroring `api_resume_extract`'s shape exactly (validate, call the shared function via
`asyncio.to_thread` since the LLM call is blocking, surface `ValueError` as a 400):

```python
class StructuredProposeRequest(BaseModel):
    domain: str
    steps: list[str] = Field(default_factory=lambda: ["grain", "bridge", "mapping"])
    redo: bool = False
    model: str | None = None

@app.post("/api/structured/tables/{table}/propose")
async def api_structured_propose(table: str, payload: StructuredProposeRequest):
    _validate_artifact_segment(table)
    _validate_artifact_segment(payload.domain)
    if structured_registry.get_table(table, domain=payload.domain) is None:
        raise HTTPException(status_code=404, detail=f"table '{table}' not found")
    try:
        result = await asyncio.to_thread(
            propose_table_semantics, table, payload.domain,
            steps=payload.steps, redo=payload.redo, model=payload.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _camelize(result)
```

`propose_table_semantics` is the same function `db propose --step --redo` calls (§6) — the admin-ui
is a caller, not a reimplementation, matching every other Lane B route in this file.

**Frontend:** the Tables table (`dashboard.js:588` `refreshStructuredTables`) gains a "Classify"
column rendering three `pip()`s per row (reusing the function verbatim). The existing row-click
expand (already wired to show columns/mappings) gains a classification block above that sub-table:
grain value + reason, the bridge-columns list (now available per §12.1), and a "Classify" button
with `--step` checkboxes + a "Redo" checkbox — wired exactly like `showChunkGrid`'s "Resume
extraction" button (`dashboard.js:271-288`): disable + relabel while in flight, re-fetch and
re-render the row's detail on success, `alert()` on failure.

### 12.3 Bulk: classify every unclassified table in a domain

No new job/worker/table — a synchronous loop over one domain's tables, run inside the request:

```python
class StructuredProposeAllRequest(BaseModel):
    domain: str
    redo: bool = False
    model: str | None = None

_bulk_classify_progress: dict[str, dict] = {}  # domain -> {"done", "total"} — in-memory only,
                                                # not persisted; a server restart mid-run just
                                                # loses the progress readout, not any table's
                                                # actual status (that's in the registry already)

@app.post("/api/structured/propose-all")
async def api_structured_propose_all(payload: StructuredProposeAllRequest):
    _validate_artifact_segment(payload.domain)
    tables = structured_registry.list_tables([payload.domain])
    if not payload.redo:
        tables = [
            t for t in tables
            if t.get("grain_status") != "ok" or t.get("bridge_status") != "ok"
            or t.get("mapping_status") != "ok"
        ]
    _bulk_classify_progress[payload.domain] = {"done": 0, "total": len(tables)}
    results = []
    for t in tables:
        try:
            r = await asyncio.to_thread(
                propose_table_semantics, t["table_name"], payload.domain,
                redo=payload.redo, model=payload.model,
            )
            results.append({"table": t["table_name"], **r})
        except Exception as exc:
            results.append({"table": t["table_name"], "error": str(exc)})
        _bulk_classify_progress[payload.domain]["done"] += 1
    _bulk_classify_progress.pop(payload.domain, None)
    return _camelize({"domain": payload.domain, "results": results})

@app.get("/api/structured/propose-all/progress")
async def api_structured_propose_all_progress(domain: str):
    return _camelize(_bulk_classify_progress.get(domain, {"done": 0, "total": 0}))
```

The main `POST` blocks until every table in the loop is done and returns the full summary; a
*second*, concurrent request to the `progress` endpoint can read `_bulk_classify_progress` mid-loop
(FastAPI serves both concurrently since the blocking work happens inside `asyncio.to_thread`) — this
is the "lightweight, non-persisted progress counter" from the widget-scope decision (§9.5), not a
job record.

**Frontend:** a "Classify all unclassified" button next to the domain selector. On click: fire the
`POST`, immediately start a `setInterval` polling `/api/structured/propose-all/progress` every ~1s
to update a "Classifying 3/12…" label, stop polling when the `POST`'s promise resolves, then call
`refreshStructuredTables()` once to reflect every table's updated pips. A checkbox toggles `redo`
(default off, matching `db propose`'s own default of skipping already-`ok` steps).

## 13. Build order (increments)

1. `grain_status`/`bridge_status`/`mapping_status` columns + idempotent migration (§4).
2. Mapping step rewritten: schema-driven prompt (§3), LLM call, confidence floor, confirm-gate —
   ship inside `semantics.py` alongside grain/bridge as a third independent function, not a merged
   call (each step is its own LLM call; see §4's status-per-step rationale — a shared call would
   re-couple exactly the resumability this spec exists to add).
3. `db propose --step`/`--redo` CLI surface + run-status reporting; retire `--skipSemantics`;
   `propose_table_semantics` becomes the one shared function the CLI, and later the admin-ui, both
   call.
4. Pipeline wiring: first-registration auto-run of all three steps with independent status
   recording; `db refresh` triggers `bridge`/`mapping` for new columns only on a `replace`-mode
   column-set change, `grain` stays manual-only on refresh (§5).
5. Retire `mappings.py`'s deterministic matcher and `test_structured_mappings.py`; re-home its
   edge-case coverage into the semantics test suite.
6. Admin-ui: fix the missing `bridge_columns` in the schema endpoint (§12.1), per-table
   classify/redo (§12.2), bulk classify-all-unclassified with progress polling (§12.3).
7. `artmind-ingestion-helper` structured-tables section (§6/§9.4).
8. Once shipped: revisit `docs/CAPABILITIES.md` 5.4/5.5 wording and grounding notes to reflect the
   new mechanism (genuinely LLM-proposed, schema-driven, no live-KG dependency, per-step
   resumable), and consider a new row for the admin-ui classification widget itself (10.5's
   "Admin console" statement doesn't yet enumerate this capability).
