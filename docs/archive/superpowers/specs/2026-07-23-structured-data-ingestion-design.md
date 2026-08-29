# Structured Data Ingestion (xlsx/csv → DuckDB) with a KG Metadata Layer

**Status:** Design — approved in brainstorming, pending spec review
**Date:** 2026-07-23
**Owner:** Surjit Das

## 1. Purpose

artmind today ingests *documents*: chunk → LLM extraction → domain-scoped
knowledge graph in Neo4j. This feature adds ingestion of **tabular data** (xlsx,
csv) into a **separate structured store** with real SQL, while the knowledge
graph gains a **metadata layer** describing those tables so that a single
natural-language question can draw on both stores when they belong to the same
domain.

Two usage shapes drive the design (both wanted; row-level fusion explicitly *not*
wanted):

- **A — enrich the graph with structured facts.** "Total balance across
  SmartSaver accounts" pulls numbers from a table but resolves *SmartSaver*
  against the graph's PRODUCT entities.
- **B — two stores, one domain, mostly independent queries.** Analytical
  questions ("average X by month") run against SQL; narrative/relationship
  questions run against the graph; the KG holds a *catalogue* of what tables and
  columns exist so a router knows where to send each question.

**Non-goal:** row-level fusion — individual spreadsheet rows do **not** become
graph nodes.

## 2. Guiding principles (the "seams")

1. **The structured store is a first-class, independently-queryable system**, not
   artmind's private appendage. In an enterprise the RDBMS/OLTP/warehouse already
   exists and is queried directly by other tools. artmind must expose a direct
   SQL surface (`db sql`) that involves the graph not at all.
2. **The KG is a metadata/semantic layer *over* the database, not a copy of it.**
   The graph holds catalogue + column profiles + column→entity mappings. It never
   holds rows.
3. **The metadata layer is built by consuming `artmind query`.** When the
   profiler proposes `column → entity_class` mappings, it is itself a client of
   the graph query CLI for that domain — it asks "what PRODUCT entities exist in
   `banking`?" and matches the column's profiled values against them.
4. **The backing database sits behind a connector abstraction.** DuckDB-over-
   parquet is the *default embedded reference adapter*. External adapters
   (Postgres/Snowflake/…) are a later increment; the interface is designed for
   them now.
5. ~~**Domain is the unifying scope across both stores.** Every table is assigned a
   domain; hybrid queries are always domain-bounded; the resolver only matches a
   column's values against entities in the same domain.~~
   > **Superseded (2026-07-25)** by
   > [`2026-07-25-cross-store-join-model-design.md`](2026-07-25-cross-store-join-model-design.md).
   > This principle breaks on hierarchical domains: documents live in `banking.policy`,
   > `banking.cases`, … while tables live at bare `banking`, so "the same domain" never
   > matches and structured data becomes invisible to per-domain discovery. `domain` is
   > now a query filter and an extraction-schema scope only; `entity_class` carries
   > routing, and fusion runs on values.

## 3. Enrichment depth: Level 1 at ingest, Level 2 at query time

- **Level 1 (ingest-time, persisted):** catalogue + per-column profiles +
  column→entity-class mapping hints. The KG "knows" that
  `products.product_name` contains {SmartSaver, SmartSaver Plus, …} and that this
  column maps to the PRODUCT class — without storing a node per value.
- **Level 2 (query-time, ephemeral):** resolving a phrase in a question
  ("SmartSaver") to a canonical key and matching it against both the column's
  profiled values and graph entity names. **No anchor nodes are persisted** — the
  only thing linking the two stores at rest is shared strings scoped by domain,
  resolved on demand. This avoids graph bloat and any sync problem on re-ingest.
  > **Amended (2026-07-25):** "scoped by domain" no longer holds — see
  > [`2026-07-25-cross-store-join-model-design.md`](2026-07-25-cross-store-join-model-design.md).
  > The rest of Level 2 stands and is now the *primary* fusion mechanism, verified
  > end to end against the banking corpus.

## 4. Storage: three homes

Each kind of metadata lives where it is authoritative. The KG holds a *derived*
projection of the semantic slice.

| Home | Holds | Authoritative for |
|---|---|---|
| **DuckDB + parquet** (data dir) | The rows; physical schema (column names, types) | *What the data actually is* |
| **Registry DB** (existing SQLite in data dir) | Operational bookkeeping **and durable column mappings** | Binding + human-confirmed config |
| **KG (Neo4j)** | Derived semantic catalogue subgraph (distinctly labeled) | *Queryable* view of tables/columns/mappings |

The domain schema YAML is **untouched** by this feature — it remains purely about
document extraction.

### 4.1 DuckDB + parquet layout

```
$ARTMIND_DATA_DIR/structured/
  artmind.duckdb                     # DuckDB catalog (views, attaches parquet)
  <domain>/<table>.parquet           # one parquet file per table, overwritten on refresh
```

- One DuckDB file per host; parquet is the on-disk format per table (columnar,
  portable, snapshot-friendly).
- Temporal tables (§7) keep history *inside* the parquet as SCD-2 rows, not as a
  parallel version chain of files.

### 4.2 Registry DB — new tables

Added to the existing SQLite registry (alongside `documents`, `ingestion_jobs`):

- `datasources(name PK, type, path_or_dsn, created_at)` — connection identity.
  **Domain-agnostic** (an external DB may span domains).
- `tables(id PK, datasource, table_name, domain, source_file, sheet, parquet_path,
  version, row_count, refresh_mode, business_key, effective_date_column,
  ingested_at, sha256)` — **carries `domain`**.
- `columns(table_id FK, name, dtype, profile_json)` — profile = distinct-value
  sample + cardinality for categoricals; min/max/null-rate for numerics.
- `column_mappings(table_id FK, column, entity_class, confirmed, confidence,
  updated_at)` — **the single durable home for mappings.** No YAML, no sidecar
  file.

### 4.3 KG catalogue subgraph (derived, distinctly labeled)

Projected from parquet (physical) + registry (confirmed mappings) on every
metadata build. A **rebuild wipes and re-projects freely** because nothing
authoritative lives in the graph.

- `(:Dataset|:Table {name, domain, row_count, parquet_path, datasource})`
- `(:TableColumn {name, dtype, profile})`
- `(:Table)-[:HAS_COLUMN]->(:TableColumn)`
- `(:TableColumn)-[:MAPS_TO_CLASS]->` entity class (and optionally
  `-[:RESOLVES_AGAINST]->` sampled entity nodes)
- Scoped to the table's domain.

Distinct labels keep catalogue plumbing out of normal domain-entity queries
(`query graph pattern*`, vector search) while remaining traversable by the
resolver.

## 5. Connector abstraction

A small interface so the backing store is pluggable. **v1 ships the DuckDB
adapter only**; the interface is defined so external adapters slot in later.

```
class Datasource(Protocol):
    def introspect_schema(self, table) -> list[Column]      # names + types
    def profile_columns(self, table) -> dict[col, Profile]  # distincts/min/max/nulls
    def run_sql(self, sql) -> Rows                           # direct, no LLM
    def load_table(self, path, table, ...) -> None           # populate (embedded only)
```

`artmind db connect <dsn>` is a **defined-but-stubbed** command in v1 (reserves
the surface; errors "external adapters not available in v1 — DuckDB only").

## 6. Ingestion pipeline

`artmind ingest <file>` **auto-detects by file type and dispatches**: csv/xlsx →
structured-store population; everything else → the existing KG pipeline. There is
no user-facing `db ingest` verb. Reuses the existing staged worker/job model.

**Phase 1 — stage & profile:**
1. Load raw table(s) into parquet + register in DuckDB.
2. Infer columns + types; profile each column.
3. **Auto-propose mappings:** for each candidate key column, query the domain KG
   (via `artmind query`) for entities and match profiled distinct values against
   entity names — exact then fuzzy/embedding — emitting `column → entity_class`
   proposals with confidence. Unmatched columns stay unmapped (pure analytical).
4. Write registry rows (`tables`, `columns`, `column_mappings` as `proposed`).

**Review gate (optional):** `artmind db mappings <table>` lists proposed vs
confirmed with confidences; `set`/`confirm`/`clear` edit rows. `--accept-proposed`
(or a confidence threshold) confirms all for bulk/non-interactive loads. Review
operates on **registry rows, not a file.**

**Phase 2 — commit:** project the catalogue subgraph into Neo4j (§4.3) scoped to
the table's domain.

### 6.1 Multi-sheet xlsx

- csv = one table; each **xlsx sheet = one table** (own parquet, own registry
  rows, same domain).
- Naming: `<filestem>` for single-sheet; `<filestem>__<sheet>` when multiple;
  sanitized to a valid SQL identifier. `--table <name>` overrides; `--sheet
  <name>` ingests just one.
- First row = header (`--headerRow N` to override).
- v1 assumes reasonably tabular sheets; genuinely messy/multi-row-header files
  **error out** with guidance to clean them first (out of scope — the `xlsx`
  skill is the tool for that). Empty/hidden sheets skipped.

## 7. Refresh & temporality

Two per-table refresh modes; the mode is registry metadata.

### 7.1 `replace` (default)

Re-ingest replaces the table's contents (not append; `--append` is a later
YAGNI). One parquet file, overwritten. `version` counter + `ingested_at` bumped.
Confirmed mappings carry forward; only new/type-changed columns re-flagged.

### 7.2 `temporal` (SCD Type-2, opt-in, built in v1)

Mirrors the graph's `valid_from`/`valid_to`/`superseded_by` model so temporality
is consistent across both stores. DuckDB has **no built-in temporal tables**, but
the SCD-2 logic is a small set of SQL statements (`hash()`, anti-joins,
`INSERT … ON CONFLICT`/update).

Requires per-table config: a `business_key` (column(s) identifying the same
logical row across refreshes) and an optional `effective_date_column`.

System columns on the parquet table: `_valid_from`, `_valid_to`, `_is_current`.

On a temporal refresh, diff incoming vs current rows by business key (hash over
non-key columns):
- *new key* → insert (`_valid_from` = effective date, `_valid_to` = null,
  `_is_current` = true);
- *same key, changed hash* → close current row (`_valid_to` = effective date,
  `_is_current` = false) **and** insert the new version;
- *unchanged* → no-op;
- *disappeared key* → soft-close (`_valid_to` = effective date).

**Effective-date source:** the designated `effective_date_column` if present,
else the batch/ingest date — mirroring how the graph derives `valid_from` from a
document field and falls back to ingestion date.

**Query exposure:** `db sql` sees all history; a `<table>_current` view exposes
`_is_current = true`; `text2sql`/hybrid honor `--asOf <date>` by filtering
`_valid_from <= asOf < _valid_to` — **the same `--asOf` the graph query already
defaults to.** A hybrid "as of last quarter" question is therefore coherent
end-to-end.

## 8. Query surface

Composable primitives; the `artmind-query` skill is the router/fuser (matching how
the query layer already composes graph patterns + `text2cypher`). No monolithic
`hybrid` command in v1.

**`artmind db` group (manage/read the structured store):**
- `db list` — datasources/tables (domain-scoped).
- `db schema [table]` — catalogue/table/column context (the schema an LLM needs
  to write SQL).
- `db sql "<SQL>"` — raw SQL, **no LLM**; the independent-query guarantee.
- `db mappings <table>` (+ `set`/`confirm`/`clear`) — review/edit mappings.
- `db refresh <table>` — re-ingest an already-registered table.
- `db connect <dsn>` — stubbed in v1 (§5).

**Under the existing `query` group (retrieval):**
- `query text2sql "<question>"` — NL→SQL against a datasource, mirroring
  `text2cypher`; uses `db schema` as context; returns SQL + rows.
- `query resolve-key "<phrase>" --column …` — value↔entity resolver
  (exact→fuzzy→embedding); returns the canonical key.

**Fusion (skill-orchestrated):** the `artmind-query` skill reads the question,
decides graph-only / SQL-only / hybrid, and for hybrid calls
`resolve-key` → `text2sql`/`db sql` → optionally a graph pattern → synthesizes the
combined answer in its LLM turn. All hybrid queries are domain-scoped and honor a
consistent `--asOf` across both stores.

CLI conventions unchanged: `camelCase` options, repeatable/comma-splittable
`--domain`, `--compact` JSON.

## 9. Skill changes

- **`artmind-query`** updated to route graph / SQL / hybrid and to compose the new
  primitives for fusion.
- The `db`/`query` help text, the skill, and the `justfile` recipes updated
  together (per repo convention on doc/code drift).

## 10. Testing implications

- Unit tests drive Click via `CliRunner` with DuckDB against tmp parquet — fast
  and hermetic, no Neo4j/network. SCD-2 diff logic is directly testable in-process
  (deterministic SQL).
- End-to-end (metadata build, hybrid fusion) needs a live Neo4j with
  `ARTMIND_NO_PROXY=1` (or a freshly restarted daemon) — the running `serve`
  daemon serves stale code otherwise.
- New registry tables must be created by `artmind init` / setup migration.

## 11. Out of scope for v1

- External datasource adapters (Postgres/Snowflake/…) — interface defined,
  `db connect` stubbed.
- `--append` refresh mode.
- Auto-cleaning of messy/multi-row-header spreadsheets.
- Row-level fusion (rows as graph nodes) and persisted Level-2 anchor nodes.
- A monolithic `query hybrid` command (skill orchestrates instead).

## 12. Build order (increments)

1. Registry schema + DuckDB adapter + `ingest` dispatch + parquet load
   (`replace` mode) + `db sql`/`db list`/`db schema`.
2. Profiling + auto-propose mappings (consuming `artmind query`) + `db mappings`
   review + registry persistence.
3. Catalogue subgraph projection into Neo4j (Level 1).
4. `query text2sql` + `query resolve-key` (Level 2).
5. `temporal` refresh mode (SCD-2) + `--asOf` alignment.
6. `artmind-query` skill routing/fusion updates.
