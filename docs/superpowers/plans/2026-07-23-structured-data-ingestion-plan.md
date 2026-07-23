# Structured Data Ingestion (xlsx/csv → DuckDB) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Every task is TDD: write the failing test first, then the code, then commit.

**Goal:** Add ingestion of tabular data (csv/xlsx) into a first-class, independently-queryable **structured store** (DuckDB over parquet), and give the knowledge graph a **derived metadata catalogue** describing those tables — so one domain can span two stores (graph + SQL) and a router/fuser skill can send each question to the right one. Rows never become graph nodes.

**Architecture:** A new `artmind/structured/` package holds the whole feature behind a `Datasource` connector abstraction (DuckDB is the only v1 adapter). The existing SQLite registry (`artmind/db.py`) gains four durable tables (`datasources`, `tables`, `columns`, `column_mappings`) — the authoritative home for bindings and human-confirmed column→entity-class mappings. Parquet files (one per table) hold the rows; a single `artmind.duckdb` catalog attaches them. Neo4j gains a **distinctly-labelled** catalogue subgraph (`:Table`, `:TableColumn`, `:EntityClass`) projected from parquet + registry — a rebuild wipes and re-projects because nothing authoritative lives there. `artmind ingest <file>` auto-detects by extension and dispatches structured files to the new pipeline; everything else flows through the existing KG pipeline unchanged. A new `db` command group manages/reads the store (`db list/schema/sql/mappings/refresh/connect`); two new `query` subcommands (`text2sql`, `resolve-key`) add NL→SQL and value↔entity resolution, with `text2sql.py` mirroring `text2cypher.py` exactly. Temporal tables use SCD-2 rows inside parquet (`_valid_from`/`_valid_to`/`_is_current`), honouring the same `--asOf` the graph query already defaults to. The `artmind-query` skill becomes the router/fuser.

**Tech Stack:** Python ≥3.14.4 (managed with `uv`), Click CLI (`CliRunner` tests), DuckDB + parquet (new dep), `openpyxl` for xlsx sheet reads (new dep), SQLite registry (`artmind/db.py`), Neo4j (catalogue projection only), LLM via `artmind.extraction.call_llm` (reused by `text2sql`). Tests are hermetic — DuckDB against **tmp parquet**, no Neo4j, no network.

---

## Background the implementer needs

Read `CLAUDE.md` at the repo root first — especially "Installed, not run from the checkout" and "Testing implications". Load-bearing facts for this plan:

- `artmind` is installed globally via `just dev-install` (editable). Python edits are live, but a running `serve` daemon serves **stale** code and the `_entry` proxy routes `query` calls to it. End-to-end checks of `query text2sql`/`resolve-key` must use `ARTMIND_NO_PROXY=1` or `just dev-stop-daemons` first. Unit tests bypass all of this (they import modules and drive Click via `CliRunner`).
- Run the suite with `just dev-test` (= `uv run --group dev pytest test/ -v`). Tests live in `test/` (**singular**). They import modules directly and mock externals; **no Neo4j and no network** are available. New tests must keep that property — DuckDB runs fully in-process against tmp files, so it is fine; anything touching Neo4j must be monkeypatched.
- CLI options are `camelCase` on the command line mapped to `snake_case` Python params (e.g. `--headerRow` → `header_row`, `--asOf` → `as_of`, `--acceptProposed` → `accept_proposed`). `--domain` is repeatable and comma-splittable via `_parse_domains` / `normalize_domains`; commands support `--compact` JSON via `_echo_json(payload, compact)`.
- After changing any CLI help or skill, update the group docstring, the skill in `artmind/skills/`, and the `justfile` recipe **together** (CLAUDE.md "Docs and code drift"), then `artmind init` to reseed the run folder. `just dev-cli-help` dumps the real command hierarchy — trust it over prose.
- **Two roots** (`paths.py`): the **run folder** `$ARTMIND_HOME` (config/skills/schemas) and the **data dir** `$ARTMIND_DATA_DIR` (ingestion artifacts). The structured store lives under the **data dir**: `$ARTMIND_DATA_DIR/structured/`. Add its constant to `paths.py`.
- The registry SQLite lives at `paths.DB_PATH`. `artmind/db.py:_init_db()` is the single migration point — it runs on every `_get_db()` and on `artmind init`/`setup`. **New tables must be created there** so `init`/setup and every command get them. Tests patch `db.DB_PATH` to a tmp file (see `test/test_jobs_stage_only.py`).
- The domain schema YAML is **untouched** by this feature — it remains purely about document extraction.
- **Quote SQL identifiers.** The new `column` column in `column_mappings` and the table names `tables`/`columns` are legal in SQLite but reserved-ish; always double-quote identifiers wherever SQL is string-built (registry DDL/DML and every DuckDB query — column names may also contain spaces). Keep the profiler's `"<col>"` quoting discipline everywhere.

### Patterns to mirror exactly (verified against the tree)

| Concern | Existing reference | Mirror it in |
|---|---|---|
| NL→query layer | `artmind/text2cypher.py` (`generate_cypher`, `execute_text2cypher`, `validate_read_only`, prompt builder, dry-run, `_run_read_query`) | `artmind/text2sql.py` |
| Registry table + migration | `artmind/db.py:_init_db()` (`ingestion_jobs`, `stage_only` migration at `db.py:131-135`) | new tables + `PRAGMA table_info` migrations |
| Registry CRUD helpers | `artmind/jobs.py` (`_create_job`, `_update_job_status`, connection open/close via `_get_db()`) | `artmind/structured/registry.py` |
| Ingest orchestration | `artmind/ingest.py` (`ingest_file` → `ingest_to_kg`; sha256 dedup; `_register_document`) | `artmind/structured/pipeline.py` |
| Worker dispatch | `artmind/worker.py:_process_job` (`ingest_file` → `ingest_to_kg`) | branch on file type |
| Neo4j MERGE style | `artmind/ingest.py:_write_to_neo4j` (`MERGE (d:Document {id}) SET d += $props`) | `artmind/structured/catalogue.py` |
| Neo4j **write session** | `artmind/graph_query.py:neo4j_session(access_mode="WRITE")` (context manager) — `_write_to_neo4j` builds its **own** driver from env; the catalogue writer should use this helper, NOT a hand-rolled driver | `catalogue.py` imports `neo4j_session` from `graph_query` |
| Neo4j constraints/indexes | `artmind/setup.py:_setup_neo4j` | add catalogue constraints |
| CLI group + subcommands | `artmind/cli.py` `@cli.group() def query()` / `@query.group() def graph()`; `_echo_json`, `_parse_domains` | `@cli.group() def db()` |
| Consuming `artmind query` | `artmind/text2cypher.py` importing `graph_query.entity_listing` | mapping proposer imports `graph_query.entity_listing` (in-process, not shelling out) |

**Consuming `artmind query` (principle #3 in the spec):** the mapping proposer "is a client of the graph query CLI". Implement this **in-process** by importing `artmind.graph_query.entity_listing(domains)` (the same function the `query graph entity-listing` command calls), NOT by shelling out to the CLI (which would hit the `_entry` proxy and a stale daemon). This keeps tests hermetic — the proposer test monkeypatches `entity_listing`.

---

## File Structure

**New package `artmind/structured/`:**
- `__init__.py` — `STRUCTURED_EXTENSIONS = {".csv", ".xlsx", ".xlsm"}`, `is_structured_source(path) -> bool`, `sanitize_identifier(name) -> str`.
- `connector.py` — `Column`, `Profile` dataclasses; `Datasource` `Protocol` (the pluggable interface).
- `duckdb_adapter.py` — `DuckDBDatasource` implementing `Datasource`; `structured_db_path()`, `parquet_path_for(domain, table)`.
- `registry.py` — SQLite CRUD for `datasources`/`tables`/`columns`/`column_mappings` (opens via `db._get_db()`).
- `profile.py` — `profile_column(values, dtype) -> Profile` (distinct-sample + cardinality for categoricals; min/max/null-rate for numerics).
- `mappings.py` — `propose_mappings(table_id, domains) -> list[dict]` (consumes `graph_query.entity_listing`, exact→fuzzy match).
- `pipeline.py` — `ingest_structured_file(path, domain, ...) -> dict` (load → profile → propose → write registry); `refresh_table(table_name, domain) -> dict`.
- `catalogue.py` — `project_catalogue(domain) -> dict` (wipe + re-MERGE the catalogue subgraph for a domain).
- `scd2.py` — `apply_scd2_refresh(con, table, incoming_rel, business_key, effective_date, ...) -> dict`; `asof_view_sql(table, as_of) -> str`.

**New top-level module (mirrors `text2cypher.py`):**
- `artmind/text2sql.py` — `generate_sql`, `execute_text2sql`, `validate_read_only_sql`, `build_text2sql_prompt`, `_schema_summary_sql`.
- `artmind/resolve_key.py` — `resolve_key(phrase, table, column, domains) -> dict` (exact→fuzzy→embedding value↔entity resolver).

**Modified:**
- `paths.py` — add `STRUCTURED_DIR = DATA_DIR / "structured"`.
- `artmind/db.py` — DDL + migrations for the four new tables in `_init_db()`.
- `artmind/setup.py` — create `STRUCTURED_DIR` in `scaffold_run_folder`; add catalogue constraints in `_setup_neo4j`.
- `artmind/cli.py` — new `db` group (`list`/`schema`/`sql`/`mappings`[`/set`/`/confirm`/`/clear`]/`refresh`/`connect`); `query text2sql`, `query resolve-key`; dispatch in `ingest_sync`.
- `artmind/worker.py` — dispatch structured files in `_process_job`.
- `artmind/ingest.py` — expose the dispatch predicate use (import `is_structured_source`); no behaviour change to KG path.
- `pyproject.toml` — add `duckdb>=1.0.0`, `openpyxl>=3.1.0` to `dependencies`.
- `artmind/skills/artmind-query/SKILL.md` — routing/fusion section (Phase 6).
- `justfile` — `db-*` recipes (Phase 1/2), `query-text2sql` recipe (Phase 4).

**New tests (all hermetic):** `test/test_structured_registry.py`, `test/test_structured_duckdb_adapter.py`, `test/test_structured_dispatch.py`, `test/test_db_cli.py`, `test/test_structured_profile.py`, `test/test_structured_mappings.py`, `test/test_db_mappings_cli.py`, `test/test_structured_catalogue.py`, `test/test_text2sql.py`, `test/test_resolve_key.py`, `test/test_structured_scd2.py`, `test/test_structured_refresh_cli.py`.

---

# Phase 1 — Registry schema + DuckDB adapter + `ingest` dispatch + `replace` load + `db sql`/`db list`/`db schema`

Delivers the spine: a csv/xlsx file becomes parquet + registry rows + an attachable DuckDB catalog, queryable with raw SQL. No profiling, no mappings, no Neo4j yet.

## Task 1.1: Add the data-dir constant and the four registry tables

**Files:**
- Modify: `paths.py` (add `STRUCTURED_DIR`)
- Modify: `artmind/db.py` (`_init_db` — DDL + migrations)
- Modify: `artmind/setup.py` (`scaffold_run_folder` — mkdir `STRUCTURED_DIR`)
- Test: `test/test_structured_registry.py`

- [ ] **Step 1: Write the failing test** — assert the tables exist after `_init_db()` with a patched `db.DB_PATH`:

```python
def test_init_db_creates_structured_tables(tmp_path, monkeypatch):
    import sqlite3, artmind.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    db._init_db()
    conn = sqlite3.connect(db.DB_PATH)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"datasources", "tables", "columns", "column_mappings"} <= names
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tables)")}
    assert {"id","datasource","table_name","domain","parquet_path","version",
            "row_count","refresh_mode","business_key","effective_date_column",
            "ingested_at","sha256","source_file","sheet"} <= cols
```

- [ ] **Step 2: Add `STRUCTURED_DIR` to `paths.py`** under the ingestion side (near `KG_DIR`):

```python
STRUCTURED_DIR = DATA_DIR / "structured"   # DuckDB catalog + <domain>/<table>.parquet
```

- [ ] **Step 3: Add the DDL to `artmind/db.py:_init_db()`** (after the `update_drafts` CREATE, before the migration block). Match the existing quoting/indentation:

```sql
CREATE TABLE IF NOT EXISTS datasources (
    name        TEXT PRIMARY KEY,
    type        TEXT NOT NULL,              -- 'duckdb' in v1
    path_or_dsn TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tables (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    datasource             TEXT NOT NULL REFERENCES datasources(name),
    table_name             TEXT NOT NULL,
    domain                 TEXT NOT NULL,
    source_file            TEXT,
    sheet                  TEXT,
    parquet_path           TEXT NOT NULL,
    version                INTEGER NOT NULL DEFAULT 1,
    row_count              INTEGER,
    refresh_mode           TEXT NOT NULL DEFAULT 'replace',   -- 'replace' | 'temporal'
    business_key           TEXT,             -- comma-joined column names (temporal only)
    effective_date_column  TEXT,
    ingested_at            TEXT NOT NULL,
    sha256                 TEXT,
    UNIQUE(datasource, table_name)
);
CREATE TABLE IF NOT EXISTS columns (
    table_id     INTEGER NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    dtype        TEXT NOT NULL,
    profile_json TEXT,
    PRIMARY KEY (table_id, name)
);
CREATE TABLE IF NOT EXISTS column_mappings (
    table_id     INTEGER NOT NULL REFERENCES tables(id) ON DELETE CASCADE,
    column       TEXT NOT NULL,
    entity_class TEXT NOT NULL,
    confirmed    INTEGER NOT NULL DEFAULT 0,   -- 0 = proposed, 1 = confirmed
    confidence   REAL,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (table_id, column, entity_class)
);
```

Migrations block: these are guarded by `CREATE TABLE IF NOT EXISTS`, so no `PRAGMA` back-fill is needed for a first cut. (If a later phase adds a column, follow the `stage_only` pattern at `db.py:131-135`.)

- [ ] **Step 4: mkdir `STRUCTURED_DIR` in `scaffold_run_folder`** — add `STRUCTURED_DIR` to the imports from `paths` and to the `for directory in (...)` mkdir loop in `artmind/setup.py`.

- [ ] **Step 5:** run `uv run --group dev pytest test/test_structured_registry.py -v` → PASS. Commit.

## Task 1.2: Registry CRUD helpers

**Files:** Create `artmind/structured/__init__.py`, `artmind/structured/registry.py`; Test: `test/test_structured_registry.py`

- [ ] **Step 1: `artmind/structured/__init__.py`** — extension set + helpers:

```python
from pathlib import Path
import re

STRUCTURED_EXTENSIONS = {".csv", ".xlsx", ".xlsm"}

def is_structured_source(path: Path) -> bool:
    return path.suffix.lower() in STRUCTURED_EXTENSIONS

def sanitize_identifier(name: str) -> str:
    """Reduce an arbitrary filestem/sheet name to a valid SQL identifier."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", name.strip()).strip("_").lower()
    if not s:
        s = "table"
    if s[0].isdigit():
        s = f"t_{s}"
    return s
```

- [ ] **Step 2: `registry.py`** — helpers opening via `db._get_db()` (mirror `jobs.py` open/commit/close):
  - `register_datasource(name, type_, path_or_dsn) -> None` (INSERT OR IGNORE, `created_at`).
  - `register_table(datasource, table_name, domain, *, source_file, sheet, parquet_path, row_count, sha256, refresh_mode="replace", business_key=None, effective_date_column=None) -> int` — UPSERT on `(datasource, table_name)`: on conflict, bump `version = version + 1`, update `row_count`/`sha256`/`ingested_at`/`parquet_path`, return `id`.
  - `get_table(table_name, domain=None) -> dict | None`; `list_tables(domains=None) -> list[dict]` (domain-scoped via `domain IN (...)` + sub-domain `LIKE dom || '.%'`).
  - `replace_columns(table_id, columns: list[dict]) -> None` (DELETE then INSERT `columns` rows; each `{name, dtype, profile_json}`).
  - `get_columns(table_id) -> list[dict]`.
  - `upsert_mapping(table_id, column, entity_class, confidence, confirmed=False) -> None`; `list_mappings(table_id) -> list[dict]`; `set_mapping_confirmed(table_id, column, entity_class, confirmed) -> int`; `clear_mappings(table_id, column=None) -> int`.
  - `delete_table(table_id) -> None`.

- [ ] **Step 3: tests** — round-trip each helper against a patched `db.DB_PATH`: register datasource+table, assert `list_tables` domain-scopes correctly, `register_table` twice bumps `version` to 2, `replace_columns`/`get_columns` round-trip, mapping upsert→confirm→list→clear. Commit.

## Task 1.3: Connector interface + DuckDB adapter

**Files:** Create `artmind/structured/connector.py`, `artmind/structured/duckdb_adapter.py`; Test: `test/test_structured_duckdb_adapter.py`

- [ ] **Step 1: `connector.py`** — the pluggable interface (spec §5):

```python
from dataclasses import dataclass
from typing import Protocol, Any

@dataclass
class Column:
    name: str
    dtype: str

@dataclass
class Profile:
    kind: str                 # 'categorical' | 'numeric' | 'other'
    distinct_sample: list     # up to N sampled distinct values (categorical)
    cardinality: int | None   # distinct count (categorical)
    minimum: Any = None       # numeric
    maximum: Any = None       # numeric
    null_rate: float | None = None

class Datasource(Protocol):
    def introspect_schema(self, table: str) -> list[Column]: ...
    def profile_columns(self, table: str) -> dict[str, Profile]: ...
    def run_sql(self, sql: str) -> list[dict]: ...           # direct, no LLM
    def load_table(self, path, table: str, *, header_row: int = 0) -> int: ...  # returns row_count
```

- [ ] **Step 2: `duckdb_adapter.py`** — `DuckDBDatasource`:
  - `structured_db_path() -> Path` = `paths.STRUCTURED_DIR / "artmind.duckdb"`; `parquet_path_for(domain, table) -> Path` = `STRUCTURED_DIR / domain / f"{table}.parquet"`.
  - `__init__(self, db_path: Path | None = None)` — `import duckdb; self.con = duckdb.connect(str(db_path or structured_db_path()))`.
  - `load_table(self, source, table, *, header_row=0) -> int`:
    - csv → `self.con.execute(f"COPY (SELECT * FROM read_csv_auto(?, header=true, skip={header_row})) TO ? (FORMAT PARQUET)", [str(source), str(parquet)])` — write parquet; then `CREATE OR REPLACE VIEW <table> AS SELECT * FROM read_parquet('<parquet>')`.
    - xlsx/xlsm → read the sheet with `openpyxl` into `list[list]` (first row = header unless `header_row`), build a DuckDB relation via `self.con.execute("CREATE OR REPLACE TABLE _stage AS SELECT * FROM (VALUES ...)")` or register column arrays; `COPY` that to parquet, then the view. (Sheet-splitting lives in the pipeline — the adapter loads one already-selected sheet.)
    - Return the parquet `row_count` (`SELECT count(*) FROM read_parquet(?)`).
  - `introspect_schema(self, table) -> list[Column]` — `DESCRIBE SELECT * FROM <table>` → `[Column(name, dtype)]`.
  - `run_sql(self, sql) -> list[dict]` — execute; return rows as dicts (`con.execute(sql).fetchdf().to_dict(orient="records")` or via `.description` + `.fetchall()` to avoid a pandas dep — prefer the manual path).
  - `profile_columns` — delegates to `profile.py` (added Phase 2); a Phase-1 stub may return `{}`.
  - `attach_all(self)` / `ensure_views(self, tables: list[dict])` — recreate a VIEW per registered parquet so a fresh connection sees every table.

- [ ] **Step 3: tests (hermetic, tmp parquet)** — write a small csv to `tmp_path`, `load_table` into a tmp DuckDB path, assert `row_count`, `introspect_schema` returns the expected columns/types, and `run_sql("SELECT count(*) ...")` returns the count. No network, no Neo4j. Commit.

## Task 1.4: Structured ingestion pipeline (`replace` mode)

**Files:** Create `artmind/structured/pipeline.py`; Test: `test/test_structured_dispatch.py`

- [ ] **Step 1: `ingest_structured_file(source: Path, domain: str, *, table: str | None = None, sheet: str | None = None, header_row: int = 0, force: bool = False) -> dict`:**
  1. sha256 of the source (reuse `artmind.ingest._compute_sha256`); dedup by `(datasource, table_name, sha256)` unless `force` — a same-hash re-ingest of an existing table is a no-op skip (return `{"status": "skipped"}`).
  2. `register_datasource("default", "duckdb", str(structured_db_path()))`.
  3. Enumerate tables: csv → one table `sanitize_identifier(table or source.stem)`; xlsx → one table per non-empty, non-hidden sheet, named `sanitize_identifier(source.stem)` (single sheet) or `<stem>__<sheet>` (multiple). `--sheet` restricts to one; `--table` overrides the single-table name. Genuinely messy sheets (no clean header row) → raise `click.ClickException` with guidance to clean via the `xlsx` skill (spec §6.1).
  4. For each table: `DuckDBDatasource().load_table(...)` → parquet + view; `register_table(...)`; `replace_columns(table_id, introspect_schema→[{name,dtype,profile_json:null}])`.
  5. Return `{"status":"ok","tables":[{table_name, domain, row_count, parquet_path, version}], ...}`.

- [ ] **Step 2: `refresh_table(table_name: str, domain: str) -> dict`** — re-run the load from the registered `source_file` for a `replace`-mode table (temporal mode wired in Phase 5). Bumps `version`. Raises if the table isn't registered.

- [ ] **Step 3: tests** — build a 2-column csv in `tmp_path`; monkeypatch `paths.STRUCTURED_DIR` / `db.DB_PATH` to tmp; call `ingest_structured_file`; assert one `tables` row, correct `row_count`, parquet exists, `columns` rows written, second call with same content is `skipped`, `force=True` bumps `version`. Commit.

## Task 1.5: `ingest` auto-detect dispatch (sync + async worker)

The dispatch hooks into **both** invocation paths. No new user-facing verb — `artmind ingest <file>` branches by extension.

**Files:** Modify `artmind/cli.py` (`ingest_sync`), `artmind/worker.py` (`_process_job`); Test: `test/test_structured_dispatch.py`

- [ ] **Step 1: `ingest_sync` (`cli.py:442`)** — inside the per-file loop, before `ingest_file`:

```python
from artmind.structured import is_structured_source
from artmind.structured.pipeline import ingest_structured_file
...
for f in files:
    if is_structured_source(f):
        res = ingest_structured_file(f, domain, force=force)
        ok_count += 1 if res.get("status") == "ok" else 0
        continue
    # ... existing KG path unchanged ...
```

- [ ] **Step 2: `worker._process_job` (`worker.py:84`)** — same branch at the top of the `for file_path_str in queued_files:` loop: if `is_structured_source(file_path)`, call `ingest_structured_file`, set the job-file row to `completed`/`failed`, `continue`. (Structured ingest has no `stage_only` waypoint — it is inherently a single commit; the flag is ignored for structured files.)

- [ ] **Step 3: tests** — `CliRunner` invoke `ingest_sync` on a tmp `.csv` with `ingest_structured_file` monkeypatched to a spy; assert the spy was called and the KG `ingest_to_kg` was **not**. Invoke on a `.txt` and assert the reverse. A structural worker test asserts `_process_job` source dispatches on `is_structured_source`. Commit.

## Task 1.6: `db` group — `list`, `schema`, `sql`

**Files:** Modify `artmind/cli.py`; Test: `test/test_db_cli.py`; also `justfile` + `artmind-query` skill note (docs-drift rule).

- [ ] **Step 1: add the group + commands** near the `query` group in `cli.py`:

```python
@cli.group()
def db():
    """Manage and read the structured (SQL) store: list/schema/sql/mappings/refresh/connect."""

@db.command("list")
@click.option("--domain", "domain", multiple=True, help="Domain(s) to scope (repeatable; comma-splittable). Omit for all.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_list(domain, compact): ...    # registry.list_tables(_parse_domains(domain) or None)

@db.command("schema")
@click.argument("table", required=False)
@click.option("--domain", "domain", multiple=True)
@click.option("--compact", is_flag=True)
def db_schema(table, domain, compact): ...   # columns + dtypes + (Phase 2) profiles + mappings; the context an LLM needs to write SQL

@db.command("sql")
@click.argument("sql")
@click.option("--compact", is_flag=True)
def db_sql(sql, compact): ...        # validate_read_only_sql(sql); DuckDBDatasource().run_sql(sql); the independent-query guarantee — NO LLM
```

- [ ] **Step 2:** `db sql` enforces read-only via `text2sql.validate_read_only_sql` (Phase 4 introduces it; for Phase 1 add a minimal local guard rejecting `INSERT/UPDATE/DELETE/CREATE/DROP/ALTER/COPY/ATTACH/PRAGMA` and fold it into `text2sql` in Phase 4). Emit `{"query_type":"sql","command":"db sql","rows":[...]}`.

- [ ] **Step 3: tests (`test/test_db_cli.py`)** — patch tmp paths, ingest a tmp csv via `ingest_structured_file`, then `CliRunner` invoke `db list` (asserts the table appears, domain-scoped), `db schema <table>` (asserts columns), `db sql "SELECT count(*) ..."` (asserts row count) and `db sql "DELETE FROM ..."` (asserts a read-only rejection, non-zero exit). Commit.

- [ ] **Step 4: docs drift** — add `db-list`, `db-schema`, `db-sql` recipes to `justfile` (using `uv run artmind db ...`); add a short "Structured store (`db`)" note to `artmind/skills/artmind-query/SKILL.md` (full routing in Phase 6); `artmind init` to reseed. Commit.

**Phase 1 acceptance:** `ARTMIND_NO_PROXY=1 artmind ingest sync data.csv --domain banking` creates `structured/banking/data.parquet` + registry rows; `db list --domain banking` shows it; `db sql "SELECT ..."` returns rows; a `.txt` still flows to the KG pipeline. `just dev-test` green.

**Phase 1 risks:** (a) DuckDB xlsx reading — the DuckDB `excel` extension needs a network install, which violates the no-network rule; **mitigation:** read xlsx via `openpyxl` (bundled dep) and hand rows to DuckDB, keeping runtime offline. (b) parquet path vs registry drift on rename — always derive `parquet_path` from `parquet_path_for(domain, table)` and store it; never recompute elsewhere. (c) `db._get_db()` opens a connection per call — fine for registry, but the DuckDB connection is separate; keep one `DuckDBDatasource` per command invocation.

---

# Phase 2 — Profiling + auto-propose mappings + `db mappings` review + persistence

Fills column profiles and proposes `column → entity_class` mappings by consuming the domain KG, persisting them as `proposed` rows; adds the review gate.

## Task 2.1: Column profiling

**Files:** Create `artmind/structured/profile.py`; wire into `DuckDBDatasource.profile_columns`; Test: `test/test_structured_profile.py`

- [ ] **Step 1: `profile_column(con, table, column, dtype, *, sample_size=25, categorical_max=200) -> Profile`:**
  - null_rate = `SELECT avg(CASE WHEN <col> IS NULL THEN 1 ELSE 0 END) FROM <table>`.
  - numeric dtype → `Profile(kind="numeric", minimum, maximum, null_rate)` via `SELECT min(<col>), max(<col>)`.
  - else → cardinality `SELECT count(DISTINCT <col>)`; if `cardinality <= categorical_max` → `Profile(kind="categorical", distinct_sample=<top sample_size distinct>, cardinality, null_rate)`; else `kind="other"` (high-cardinality free text — never a mapping candidate).
  - Column identifiers are quoted (`"<col>"`) to survive spaces; the table is a trusted registered identifier.

- [ ] **Step 2: `DuckDBDatasource.profile_columns(table)`** loops `introspect_schema` → `profile_column` → `{col: Profile}`. Pipeline stores `dataclasses.asdict(profile)` as `profile_json` in `replace_columns`.

- [ ] **Step 3: tests (tmp parquet)** — a csv with a low-cardinality string column, a numeric column, and a high-cardinality id column; assert categorical gets `distinct_sample`+`cardinality`, numeric gets `min`/`max`, id column gets `kind="other"`, null_rate correct. Commit.

- [ ] **Step 4:** update `ingest_structured_file` (Task 1.4) to profile after load and persist profiles into `columns.profile_json`; extend the Task 1.4 test to assert `profile_json` is populated. Commit.

## Task 2.2: Auto-propose mappings (consume `artmind query`)

**Files:** Create `artmind/structured/mappings.py`; Test: `test/test_structured_mappings.py`

- [ ] **Step 1: `propose_mappings(table_id: int, domains: list[str], *, fuzzy_threshold: float = 0.82) -> list[dict]`:**
  - Load the table's `columns` from the registry; consider only `kind == "categorical"` columns (candidate key columns).
  - Fetch domain entities **in-process**: `from artmind.graph_query import entity_listing; listing = entity_listing(domains)` → build `{entity_class: set(names)}` from `listing["rows"]` (`row["label"]`, `row["typeGroups"][*]["names"]`).
  - For each candidate column, match its `distinct_sample` against each class's names: **exact** (case-folded) first; then **fuzzy** (`difflib.SequenceMatcher` ratio ≥ threshold — stdlib, hermetic; embedding match is a documented later upgrade, not v1). Confidence = fraction of sampled values that matched. Emit `{column, entity_class, confidence}` for the best class per column above a floor (e.g. 0.4); unmatched columns stay unmapped.
  - Persist via `registry.upsert_mapping(table_id, column, entity_class, confidence, confirmed=False)`.

- [ ] **Step 2: tests** — monkeypatch `graph_query.entity_listing` to return a fake listing (`PRODUCT` → `{"SmartSaver","SmartSaver Plus"}`); a registry table whose `product_name` categorical sample is `["SmartSaver","SmartSaver Plus"]`; assert a `PRODUCT` proposal with confidence ≈1.0 is persisted as `confirmed=0`, and a numeric `balance` column yields no proposal. No Neo4j (entity_listing is patched). Commit.

- [ ] **Step 3:** call `propose_mappings` at the end of `ingest_structured_file` (best-effort — a down graph must not fail the load: wrap in try/except, log a warning, mirroring `commit_to_graph`'s hook guarding). Extend the dispatch test to assert proposals are attempted. Commit.

## Task 2.3: `db mappings` review command (+ `set`/`confirm`/`clear`)

**Files:** Modify `artmind/cli.py`; Test: `test/test_db_mappings_cli.py`

- [ ] **Step 1: nested group** under `db`:

```python
@db.group("mappings", invoke_without_command=True)
@click.argument("table")
@click.option("--domain", "domain", multiple=True)
@click.option("--acceptProposed", "accept_proposed", is_flag=True, help="Confirm all proposed mappings (bulk/non-interactive)")
@click.option("--compact", is_flag=True)
@click.pass_context
def db_mappings(ctx, table, domain, accept_proposed, compact):
    """List proposed vs confirmed mappings for TABLE (default action)."""
    # resolve table_id; if a subcommand was invoked, stash table_id in ctx.obj and return
    # else: if accept_proposed → set_mapping_confirmed(all); print list_mappings(table_id)

@db_mappings.command("set")
@click.option("--column", required=True)
@click.option("--entityClass", "entity_class", required=True)
@click.option("--confidence", type=float, default=1.0)
@click.pass_context
def db_mappings_set(ctx, column, entity_class, confidence): ...   # upsert confirmed=True

@db_mappings.command("confirm")
@click.option("--column", required=True)
@click.option("--entityClass", "entity_class", required=True)
@click.pass_context
def db_mappings_confirm(ctx, column, entity_class): ...           # set_mapping_confirmed(...,True)

@db_mappings.command("clear")
@click.option("--column", default=None)
@click.pass_context
def db_mappings_clear(ctx, column): ...                            # clear_mappings(table_id, column)
```

Review operates on **registry rows, not a file** (spec §6). Use `ctx.obj` to pass the resolved `table_id` to subcommands.

- [ ] **Step 2: tests** — ingest a tmp table + persist a proposed mapping; `CliRunner` invoke `db mappings <table>` (lists proposed w/ confidence), `... set --column c --entityClass PRODUCT` (adds confirmed), `... confirm ...` (flips confirmed), `... clear --column c` (removes), and `db mappings <table> --acceptProposed` (bulk-confirms). Assert registry state after each. Commit.

- [ ] **Step 3:** extend `db schema` to include each column's mapping (proposed/confirmed) so the SQL-writing LLM sees the semantic layer. Update `justfile` + skill note. Commit.

**Phase 2 acceptance:** after ingesting a categorical table into a domain with matching KG entities, `db mappings <table>` shows `column → CLASS (proposed, confidence)`; `--acceptProposed` confirms them; `db schema` surfaces confirmed mappings. `just dev-test` green.

**Phase 2 risks:** (a) fuzzy matching false positives — keep the confirm gate mandatory before catalogue projection trusts a mapping (`confirmed=1`); proposals are hints only. (b) `entity_listing` cost on huge domains — it's the same call `query graph entity-listing` already makes; acceptable, and mapping is best-effort. (c) embedding match deferred — documented, not silently dropped.

---

# Phase 3 — Catalogue subgraph projection into Neo4j (Level 1)

Projects the physical schema (parquet) + confirmed mappings (registry) into a distinctly-labelled Neo4j subgraph. A rebuild wipes and re-projects freely.

## Task 3.1: Catalogue constraints in setup

**Files:** Modify `artmind/setup.py` (`_setup_neo4j`, `setup_all` summary); Test: extend `test/test_scaffold_run_folder.py` or a structural check.

- [ ] **Step 1:** add uniqueness constraints (MERGE keys are synthetic composite `key` props to avoid enterprise node-key constraints):

```python
session.run("CREATE CONSTRAINT cat_table_key IF NOT EXISTS FOR (n:Table) REQUIRE n.key IS UNIQUE")
session.run("CREATE CONSTRAINT cat_column_key IF NOT EXISTS FOR (n:TableColumn) REQUIRE n.key IS UNIQUE")
session.run("CREATE CONSTRAINT cat_entityclass_key IF NOT EXISTS FOR (n:EntityClass) REQUIRE n.key IS UNIQUE")
session.run("CREATE INDEX cat_table_domain IF NOT EXISTS FOR (n:Table) ON (n.domain)")
```

Add these labels to the `setup_all` return summary. (Structural test asserts the DDL strings appear in `inspect.getsource(setup._setup_neo4j)` — the suite has no live Neo4j.)

## Task 3.2: `project_catalogue(domain)`

**Files:** Create `artmind/structured/catalogue.py`; Test: `test/test_structured_catalogue.py`

- [ ] **Step 1: `project_catalogue(domain: str) -> dict`** — import and use `graph_query.neo4j_session(access_mode="WRITE")` (the context manager at `graph_query.py:150`; do **not** hand-roll a driver — `_write_to_neo4j` does, but the catalogue writer should not), mirroring `_write_to_neo4j`'s MERGE style. Confirm the helper accepts a write access mode:
  1. **Wipe** the domain's catalogue nodes (nothing authoritative lives here): `MATCH (t:Table {domain:$domain}) OPTIONAL MATCH (t)-[:HAS_COLUMN]->(c:TableColumn) DETACH DELETE t, c`.
  2. For each `registry.list_tables([domain])`: MERGE the table node —
     `MERGE (t:Table {key:$key}) SET t += {name, domain, row_count, parquet_path, datasource}` with `key = f"{datasource}::{table_name}"`.
  3. For each `registry.get_columns(table_id)`: MERGE the column and edge —
     `MERGE (c:TableColumn {key:$colkey}) SET c += {name, dtype, profile} WITH c MATCH (t:Table {key:$key}) MERGE (t)-[:HAS_COLUMN]->(c)` with `colkey = f"{table_key}::{name}"`; `profile` = the JSON string.
  4. For each **confirmed** mapping (`confirmed=1`): MERGE `(:EntityClass {key:f"{domain}::{class}", name, domain})` and `(c)-[:MAPS_TO_CLASS]->(ec)`. Optionally MERGE `(c)-[:RESOLVES_AGAINST]->(:Entity)` for a small sample of matched entity nodes (looked up by name+class+domain) — mark this OPTIONAL/bounded (spec §4.3) and skip if the entity node is absent.
  5. Scope everything to the table's domain. Return counts `{tables, columns, mappings}`.

- [ ] **Step 2: tests** — patch `catalogue.neo4j_session` with a fake session recording `run(cypher, **params)` calls (same fake-session pattern as `test/test_supersession.py`); seed a tmp registry with one table + one confirmed mapping; assert the projection issues a wipe, a `MERGE (t:Table` with the right `key`, `HAS_COLUMN`, and `MAPS_TO_CLASS` for the confirmed mapping only (a `proposed` mapping is NOT projected). Fully hermetic. Commit.

## Task 3.3: Wire projection into commit + a `db catalogue` refresh

**Files:** Modify `artmind/structured/pipeline.py`, `artmind/cli.py`

- [ ] **Step 1:** after a successful `ingest_structured_file` / confirm, call `project_catalogue(domain)` best-effort (try/except + warning log — a down Neo4j must not fail the parquet load, matching the KG commit's hook guarding).
- [ ] **Step 2:** add `db catalogue --domain <d>` (rebuild-on-demand) so a mapping confirmed later can be re-projected without re-ingesting. Update `justfile` + skill. Commit.

**Phase 3 acceptance (needs live Neo4j, `ARTMIND_NO_PROXY=1`):** after ingest + `--acceptProposed`, `query graph metadata --domain <d>` still shows only domain entities (catalogue labels excluded); a direct Cypher `MATCH (t:Table {domain})-[:HAS_COLUMN]->(c) RETURN t,c` shows the catalogue; re-running `db catalogue` is idempotent (wipe+re-MERGE, no dupes). `just dev-test` green (hermetic tests only).

**Phase 3 risks:** (a) catalogue labels leaking into `query graph pattern*`/vector search — the distinct labels (`:Table`, `:TableColumn`, `:EntityClass`) and the absence of the `:Entity` label keep them out; add a test asserting the projection never sets `:Entity` on catalogue nodes. (b) `RESOLVES_AGAINST` to real `:Entity` nodes could bloat/stale — keep it bounded and OPTIONAL; the resting link between stores is shared strings, not persisted anchors (spec §3 Level-2).

---

# Phase 4 — `query text2sql` + `query resolve-key` (Level 2)

NL→SQL (mirroring `text2cypher`) and the value↔entity resolver. Both are query-time, ephemeral — no persisted anchors.

## Task 4.1: `artmind/text2sql.py` (mirror `text2cypher.py`)

**Files:** Create `artmind/text2sql.py`; Test: `test/test_text2sql.py`

- [ ] **Step 1:** mirror `text2cypher.py` function-for-function:
  - `validate_read_only_sql(sql)` — regex reject `INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|TRUNCATE|COPY|ATTACH|DETACH|PRAGMA|CALL|INSTALL|LOAD` (DuckDB write/side-effect verbs), raising `ValueError` like `validate_read_only`. Move `db sql`'s Phase-1 guard here and have `db sql` import it.
  - `_schema_summary_sql(tables: list[dict], columns_by_table, mappings_by_table) -> str` — a compact text block: per table → `TABLE <name> (domain=<d>, rows=N): col:dtype [→ CLASS], ...` — the schema an LLM needs (this is what `db schema` returns).
  - `build_text2sql_prompt(question, schema_info, domains) -> str` — DuckDB-SQL expert; rules: read-only, use only listed tables/columns, scope by domain, honour `<table>_current`/`_valid_from<=asOf` when an `--asOf` is supplied (Phase 5). Return JSON `{"sql": ..., "notes": ...}`.
  - `generate_sql(question, domains, model=None) -> dict` — build schema from `registry.list_tables(domains)` + columns + confirmed mappings; `call_llm(resolve_llm_model(env, model), prompt)`; `parse_json_response`; `validate_read_only_sql`; return `{"sql", ...}`.
  - `execute_text2sql(question, domains, model=None, dry_run=False, as_of=None) -> dict` — `{query_type:"sql", command:"text2sql", question, generated_sql, rows}`; dry-run skips execution (returns `rows:[]`, `dry_run:True`); execute via `DuckDBDatasource().run_sql(sql)`; wrap DuckDB errors with the generated SQL in the message (mirror the `Neo4jError` handling).

- [ ] **Step 2: `query text2sql` CLI** (`cli.py`, under `query` group):

```python
@query.command("text2sql")
@click.option("--domain", "domain", required=True, multiple=True)
@click.option("--asOf", "as_of", default=None)
@click.option("--dry-run", "dry_run", is_flag=True)
@click.option("--compact", is_flag=True)
@click.argument("question")
def query_text2sql(domain, as_of, dry_run, compact, question): ...
```

- [ ] **Step 3: tests (mirror `test_text2cypher.py`, hermetic)** — fake `call_llm` returning `{"sql":"SELECT ...","notes":""}`; monkeypatch `registry.list_tables`/`get_columns` for schema; assert `generate_sql` returns SQL + rejects a write SQL (`DELETE ...` → ValueError); `execute_text2sql(dry_run=True)` skips execution; `execute_text2sql` with `run_sql` patched returns rows; schema summary includes table/column/mapping. Commit.

## Task 4.2: `query resolve-key` (value↔entity resolver)

**Files:** Create `artmind/resolve_key.py`; Modify `cli.py`; Test: `test/test_resolve_key.py`

- [ ] **Step 1: `resolve_key(phrase, domains, *, column=None, table=None, top_k=5) -> dict`:** resolve a phrase to a canonical key by matching against **both** a column's profiled distinct values (registry `profile_json`) **and** graph entity names (`graph_query.entity_resolve` / `entity_listing`), in the same domain. Order: exact (case-folded) → fuzzy (`difflib`) → embedding (documented later upgrade). Return `{phrase, canonical, source: "column"|"graph"|"both", column, entity_class, candidates:[...]}`. No anchor nodes persisted (spec §3 Level-2).

- [ ] **Step 2: CLI** `query resolve-key "<phrase>" --column <col> --domain <d>` (`--column` optional; without it, resolve against the graph only).

- [ ] **Step 3: tests** — patch `entity_listing` + a registry column profile; assert `"smartsaver"` resolves to canonical `"SmartSaver"` with `entity_class="PRODUCT"`; an unknown phrase returns empty `candidates`. Hermetic. Commit.

- [ ] **Step 4: docs drift** — `justfile` `query-text2sql` recipe; document both commands in `artmind-query` skill (routing in Phase 6). Reseed. Commit.

**Phase 4 acceptance (live Neo4j+DuckDB, `ARTMIND_NO_PROXY=1`):** `query text2sql "average balance by product" --domain banking` returns SQL + rows; `query resolve-key "smartsaver" --column product_name --domain banking` returns the canonical PRODUCT key. `just dev-test` green.

**Phase 4 risks:** (a) LLM emits a write SQL — `validate_read_only_sql` is the friendly pre-check; DuckDB itself is the backstop (open the query connection read-only where feasible, or run in a fresh connection with no write intent). (b) SQL referencing unregistered tables — the prompt lists only registered tables; a DuckDB error is surfaced with the SQL for debugging.

---

# Phase 5 — `temporal` refresh mode (SCD-2) + `--asOf` alignment

Opt-in per-table SCD Type-2 history inside parquet, mirroring the graph's valid-time model, honouring the same `--asOf`.

## Task 5.1: SCD-2 refresh SQL

**Files:** Create `artmind/structured/scd2.py`; Test: `test/test_structured_scd2.py`

- [ ] **Step 1:** system columns on a temporal table's parquet: `_valid_from`, `_valid_to` (null = open), `_is_current` (bool). Config from the registry: `business_key` (comma-joined columns identifying the same logical row), optional `effective_date_column`.

- [ ] **Step 2: `apply_scd2_refresh(con, table, incoming_rel, business_key: list[str], effective_date: str, *, effective_date_column: str | None = None) -> dict`.**

  **Critical: DuckDB cannot `UPDATE`/`INSERT` a parquet-backed VIEW in place.** A temporal table is a VIEW over `read_parquet(...)`, so the refresh must **materialize the current history into a real temp table, apply the diff there, then `COPY` the full history back to parquet** — reading existing history first and never truncating it. The pseudocode below is *intent only*; the four-case tests in Step 4 are the actual contract — derive the exact set logic from them, and prefer anti-joins (`NOT EXISTS`/`LEFT JOIN … IS NULL`) over `(cols) NOT IN (…)` to stay NULL-safe with composite keys (`COALESCE` NULL key parts).

  Procedure (identifiers **quoted**; `<bk>` = business-key columns, `<nonkey>` = all non-key, non-system columns; `<eff>` = `COALESCE("<effective_date_column>", DATE '<effective_date>')` when a column is set, else the literal batch date):

```sql
-- 1. Materialize current history into a real temp table (the parquet VIEW is not writable)
CREATE OR REPLACE TEMP TABLE _cur AS SELECT * FROM read_parquet('<parquet>');
-- 2. Stage incoming rows with effective date + a change-hash over non-key columns
CREATE OR REPLACE TEMP TABLE _inc AS
  SELECT *, hash(<nonkey>) AS _h, <eff> AS _eff FROM <incoming_rel>;
-- 3. Current open versions + their hash, for comparison
CREATE OR REPLACE TEMP TABLE _cur_open AS
  SELECT *, hash(<nonkey>) AS _h FROM _cur WHERE _is_current;
-- 4. CLOSE current rows whose key changed (hash differs) OR disappeared:
--    keep-open ONLY where an incoming row has the identical non-key hash.
UPDATE _cur SET _valid_to = DATE '<effective_date>', _is_current = false
WHERE _is_current
  AND NOT EXISTS (
    SELECT 1 FROM _inc i JOIN _cur_open c USING (<bk>)
    WHERE c."<pk>" = _cur."<pk>" AND i._h = c._h);          -- adapt join to the row identity
-- 5. INSERT new + changed keys as new open versions:
--    every incoming key NOT currently open with the identical hash.
INSERT INTO _cur BY NAME
SELECT i.* EXCLUDE (_h, _eff), i._eff AS _valid_from, NULL AS _valid_to, true AS _is_current
FROM _inc i
WHERE NOT EXISTS (
  SELECT 1 FROM _cur_open c WHERE c.<bk> = i.<bk> AND c._h = i._h);
-- 6. unchanged (same key + identical hash still open) → excluded by both anti-joins → no-op
-- 7. Write the full history back, then recreate the VIEW
COPY _cur TO '<parquet>' (FORMAT PARQUET);
```

The four cases (spec §7.2): *new key* → inserted open (step 5); *same key, changed hash* → old closed (step 4) + new open (step 5); *unchanged* → matched by identical hash in both anti-joins → no-op; *disappeared key* → closed (step 4), never re-inserted. Effective-date source: `effective_date_column` if present, else the batch/ingest date (mirrors the graph deriving `valid_from` from a doc field, falling back to ingestion date). Return `{inserted, closed, unchanged}`.

- [ ] **Step 3: `asof_view_sql(table, as_of=None) -> str`** — `<table>_current` view = `WHERE _is_current`; as-of filter = `WHERE _valid_from <= :asOf AND (_valid_to IS NULL OR :asOf < _valid_to)` — the **same** `--asOf` semantics as `graph_query.asof_predicate`.

- [ ] **Step 4: tests (fully hermetic, in-process DuckDB)** — the SCD-2 diff is deterministic SQL, directly testable:
  1. Seed a temporal table (3 rows, all current). Refresh with: one unchanged row, one changed row, one new key, one disappeared key.
  2. Assert: unchanged → still one current row; changed → old row closed (`_valid_to=eff, _is_current=false`) + new current row; new key → one current row; disappeared → closed, no current row.
  3. `<table>_current` returns exactly the current set; the `--asOf` filter at a mid-history date returns the then-current versions. No Neo4j, no network. Commit.

## Task 5.2: Wire temporal mode into refresh + `db refresh`

**Files:** Modify `artmind/structured/pipeline.py`, `artmind/cli.py`; Test: `test/test_structured_refresh_cli.py`

- [ ] **Step 1:** `refresh_table` branches on `registry.get_table(...)["refresh_mode"]`: `replace` → overwrite parquet (Phase 1); `temporal` → load incoming into a staged relation, `apply_scd2_refresh(...)`, rewrite parquet with the full history, bump `version`. Confirmed mappings carry forward; only new/type-changed columns are re-flagged.
- [ ] **Step 2:** first-time temporal ingest seeds `_valid_from = eff`, `_valid_to = null`, `_is_current = true` for every row. Setting a table temporal: `db mappings`-style config or an `ingest ... --refreshMode temporal --businessKey col[,col] --effectiveDateColumn col` set of options on `ingest_sync` (camelCase). Persist to the `tables` row.
- [ ] **Step 3: `db refresh <table> [--asOf ...]` CLI** — re-ingest an already-registered table from its `source_file`. `text2sql`/hybrid honour `--asOf` by filtering `_valid_from <= asOf < _valid_to`.
- [ ] **Step 4: tests** — `CliRunner`-drive an ingest with `--refreshMode temporal --businessKey id`, then `db refresh` with a changed source, assert history rows via `db sql`. `text2sql` prompt (Task 4.1) references `<table>_current` / `--asOf`; add a test that `execute_text2sql(as_of=...)` threads the date into the prompt. Commit.

**Phase 5 acceptance:** a temporal table accumulates SCD-2 history across refreshes; `db sql "SELECT * FROM t"` sees all history; `t_current` sees only current; `query text2sql "... as of last quarter" --asOf 2026-03-31` filters by valid-time consistently with the graph. `just dev-test` green.

**Phase 5 risks:** (a) DuckDB has no native temporal tables — mitigated by the small deterministic SCD-2 SQL set; keep it in one tested function. (b) composite business keys + NULLs — normalise NULLs in the key comparison (`coalesce`), and test a composite key. (c) parquet is overwritten wholesale on refresh — history lives *inside* the parquet as rows (spec §4.1), not as a file chain; ensure the rewrite reads existing history first, never truncating it.

---

# Phase 6 — `artmind-query` skill routing/fusion

Makes the skill the router/fuser: graph-only / SQL-only / hybrid, composing the new primitives. No monolithic `hybrid` command.

## Task 6.1: Update the `artmind-query` skill

**Files:** Modify `artmind/skills/artmind-query/SKILL.md` (source of truth); then `artmind init` to reseed the run folder (the chat UI reads the copy).

- [ ] **Step 1:** add a **Route** decision to the protocol: after `domains-overview`, run `db list --domain <d>` and `db schema` to learn whether the domain has structured tables. Decide per question:
  - *narrative/relationship* → graph path (existing patterns / `text2cypher`).
  - *analytical/aggregate* ("average/total/count by X") → `query text2sql` (or `db sql` for exact SQL).
  - *hybrid* (enrich-the-graph, spec usage A) → `query resolve-key "<phrase>" --column <c>` to canonicalise, then `text2sql`/`db sql` for the numbers, optionally a graph pattern for relationships, then synthesise in the LLM turn.
- [ ] **Step 2:** all hybrid queries domain-scoped; honour one consistent `--asOf` across both stores. Add worked examples (usage A "total balance across SmartSaver accounts"; usage B "average X by month"). Document `--compact`.
- [ ] **Step 3:** update the `db`/`query` help text (group docstrings), the skill, and the `justfile` recipes together (docs-drift rule). Reseed with `artmind init`.
- [ ] **Step 4: verification** — no unit-test harness for skill prose; verify via `just dev-cli-help` that all new commands appear, and a live chat-UI smoke test (`artmind init` first, per CLAUDE.md — the chat agent reads the run-folder copy, not the checkout symlink).

**Phase 6 acceptance:** the skill routes a narrative question to the graph, an aggregate to SQL, and a hybrid question through resolve-key → text2sql → synthesise, all domain-scoped and `--asOf`-consistent.

**Phase 6 risks:** skill/CLI drift — the skill names exact commands; a renamed command silently breaks routing. Mitigation: `just dev-cli-help` is the trusted hierarchy; keep the skill's command names in sync in the same commit as any CLI change.

---

## Final verification

- [ ] **Full suite:** `just dev-test` — all existing tests plus the ~12 new files green, no Neo4j/network touched.
- [ ] **Dependencies:** `duckdb` and `openpyxl` added to `pyproject.toml` `dependencies`; `uv lock` regenerated via `uv` (never hand-edit `uv.lock`); `just dev-install` succeeds.
- [ ] **Registry migration:** delete a scratch registry, run `artmind init`, confirm the four new tables exist (they must be created by `init`/setup, per spec §10).
- [ ] **End-to-end, out of process** (per CLAUDE.md — a running daemon serves stale code): `just dev-stop-daemons`, then with Neo4j + a domain with entities:
  ```bash
  ARTMIND_NO_PROXY=1 artmind ingest sync products.csv --domain banking
  ARTMIND_NO_PROXY=1 artmind db list --domain banking
  ARTMIND_NO_PROXY=1 artmind db mappings products --acceptProposed
  ARTMIND_NO_PROXY=1 artmind db catalogue --domain banking
  ARTMIND_NO_PROXY=1 artmind db sql "SELECT count(*) FROM products"
  ARTMIND_NO_PROXY=1 artmind query text2sql "how many products" --domain banking
  ```
  Confirm the parquet + registry rows exist, the catalogue projects (distinctly labelled, excluded from `query graph metadata`), and SQL/NL-SQL answer.
- [ ] **Independent-query guarantee:** `db sql` involves the graph not at all (spec principle #1) — verify it works with Neo4j down.
- [ ] **Idempotency:** re-run `db catalogue --domain banking` twice — no duplicate `:Table`/`:TableColumn` nodes (MERGE on `key`). Re-ingest the same csv unchanged — `skipped`; `--force` bumps `version`.

## Out of scope for v1 (spec §11) — do NOT build

External adapters (Postgres/Snowflake — `db connect` is **defined-but-stubbed**: it errors "external adapters not available in v1 — DuckDB only"); `--append` refresh; auto-cleaning messy/multi-row-header spreadsheets (error out with guidance); row-level fusion (rows as graph nodes); persisted Level-2 anchor nodes; a monolithic `query hybrid` command (the skill orchestrates).

## `db connect` stub (Phase 1 or 4, tiny)

- [ ] Add `db connect <dsn>` that reserves the surface and raises `click.ClickException("external adapters not available in v1 — DuckDB only")`. One test asserts the error. Keeps the `Datasource` Protocol's external-adapter seam visible without implementing it.
