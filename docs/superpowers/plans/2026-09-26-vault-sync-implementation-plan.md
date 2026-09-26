# `vault sync` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `docs/superpowers/specs/2026-09-25-vault-sync-design.md` in full — a git-diff-driven, CDC-style `artmind vault sync` command that replays committed KG-staging document folders and committed structured-store text into Neo4j/DuckDB since the last sync, plus its two prerequisite building blocks (`registry.restore_tables`/scoped `import_structured_text`, and `ingest.retract_document`).

**Architecture:** A new `artmind/vault_sync.py` module owns the sync engine: pure git-diff classification (track A: `.artmind/data/kg/**`, track B: `.artmind/data/structured_text/**`) plus an orchestrator that sequences track B's regeneration, track A's replay/retraction, one batched projection rebuild across the union of affected keys (reusing `table2graph`'s existing chunking), domain-scoped embed sweeps, and — only on full success — advancing the `last_synced_commit` cursor in `.artmind/state.json`. Three existing modules grow small, additive extensions this depends on: `structured/registry.py` (scoped restore), `structured/text_export.py` (scoped import), `table2graph.py` (a `defer_rebuild` escape hatch), and `ingest.py` gains a new standalone retraction primitive. The CLI promotes `vault` from a bare command to a group (`status` + `sync`).

**Tech Stack:** Python 3.14, Click/rich-click, Neo4j (via existing `graph_query.neo4j_session`), DuckDB/SQLite (existing `structured` package), `git` via the existing `utils.functions.run_command` wrapper, pytest (hermetic — no real Neo4j/network, per `test/conftest.py`'s autouse guard).

**Two deliberate deviations from the spec's prose, decided during planning (both already reconciled with the spec's author via a live check):**
1. **§10's "[ingest] extra" gate is dropped.** Verified against the actual code: `duckdb` is a *core* dependency (`pyproject.toml`), not part of `[ingest]` (only `docling`/`langchain-text-splitters`/`openpyxl` are), and track B's code path (`table2graph.py`, `structured/text_export.py`, `structured/registry.py`) imports none of those three. A `_require_ingest_extra()`-style gate here would be dead code that can never fire. Task 9 below implements track B with no such gate, and the corresponding §12 test case ("query-only host, track B present, extra absent → fail loud") is not implemented, since there is nothing to test.
2. **`artmind vault`'s bare invocation is NOT preserved as a "runnable group."** The spec's §11 only requires `artmind vault status`'s *own* behavior preserved byte-for-byte, not that bare `artmind vault` (no subcommand) keeps behaving like the old single command. Making `vault` a plain `click.Group` (bare invocation shows `--help`, exit 0 — the same convention every other group in this CLI already follows) is simpler and needs no changes to `cli_guide.py`'s coverage logic. The alternative (`invoke_without_command=True`, mirroring `db mappings`) would additionally require patching `cli_guide.py`'s top-level `build_sections()` to recognize a *top-level* runnable group (today it only recognizes one nested under another group's own `COMMAND_GROUPS` entry, which is why `db mappings` works but a top-level one wouldn't) — real, but out of scope for this plan. Task 11 updates the two existing tests in `test/test_vault_cli.py` that invoke bare `["vault"]` to `["vault", "status"]` accordingly.

---

## Before you start

Read, in this order:
1. `docs/superpowers/specs/2026-09-25-vault-sync-design.md` (the spec this plan implements).
2. `docs/superpowers/specs/2026-09-25-projection-rebuild-batching-design.md` (already implemented — `projection.rebuild(tx, keys, ...)` is fast per-batch now; this plan treats it as a black box).
3. `CLAUDE.md` — "Testing implications" (in particular: assert on parameters/queries sent, never on summary counts alone; `ARTMIND_NO_PROXY=1` when manually verifying via the CLI, since `vault sync` is not proxied by `_entry.py` but old `artmind` invocations of *other* commands during manual testing might be).

Key facts this plan relies on, already verified against the current code (no need to re-derive them):
- Aggregate keys are `(canonical_name, entity_class, domain)` 3-tuples (`artmind/observations.py:150`) — **domain is the third element**, not the first.
- `ingest.commit_to_graph(doc_kg_dir, domain, defer_rebuild=False) -> bool` swallows exceptions into `False` and discards the commit summary. `vault sync` needs the summary (for `deferred_keys`) *and* needs a real exception to propagate (§9's all-or-nothing). So `vault_sync.py` calls the lower-level `ingest._write_to_neo4j(doc_kg_dir, domain, defer_rebuild=True)` directly instead — the exact same function `commit_to_graph` itself calls, already reused cross-module today by `table2graph.table_to_graph`. This is not a new pattern.
- `table2graph.table_to_graph(...)` today *always* runs its own projection rebuild (`_rebuild_in_batches`) and embed sweeps internally, even though it stages the Neo4j write with `defer_rebuild=True` under the hood. There is currently no way to get a table's *deferred keys* back without also triggering its own rebuild. Task 6 adds a `defer_rebuild` parameter to `table_to_graph` itself to close this gap — required for §5 step 2 to work as described ("run table2graph (defer_rebuild=True), collecting deferred keys").
- `table2graph.batch_keys`/`REBUILD_BATCH`/`_rebuild_in_batches` are already fully generic (operate on a bare `keys` list, no table-specific state) — §5 step 4 reuses `table2graph._rebuild_in_batches` directly rather than reimplementing chunking.
- `lifecycle.py`'s `retire_document`/`_transition` looks superficially similar to what §8 wants but is **not** reusable as-is: it always calls `projection.rebuild(tx, keys, ...)` immediately, inside its own transaction. §8's `retract_document` must instead *defer* the rebuild to the caller's batched pass (mirroring `commit_to_graph(..., defer_rebuild=True)`'s own `deferred_keys` shape), so it needs its own, simpler implementation built from `_retract_prior_version` plus one new `:Document`→`:DocumentHistory` relabel (the one label `_retract_prior_version` deliberately leaves alone, and the one line `lifecycle._transition` already proves the correct Cypher for).
- A document folder's `doc_id` cannot be derived from its path (unlike a `table__<name>` folder's, which is always `f"table:{domain}:{table_name}"`) — retraction for a removed folder must read the last-known `document.json` via `git show <last_synced_commit>:<path>`, which is *guaranteed* to succeed for any path git reports as status `D` in a `last_synced_commit..HEAD` diff (if it didn't exist at `last_synced_commit`, it can't show as removed relative to it).
- CLI options are camelCase on the command line per `CLAUDE.md` (`--dryRun`, `--asOf`, etc.) — the spec prose's `--dry-run`/`--bootstrap-empty`/`--bootstrap-synced` become `--dryRun`/`--bootstrapEmpty`/`--bootstrapSynced` in the actual flags.

---

### Task 1: The sync cursor (`.artmind/state.json`)

**Files:**
- Modify: `artmind/vault.py` (add `read_state`/`write_state`, add `import json` at top)
- Test: `test/test_vault.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault.py`:

```python
def test_read_state_is_empty_dict_when_state_json_absent(tmp_path):
    layout = vault.VaultLayout(tmp_path)
    assert vault.read_state(layout) == {}


def test_write_state_then_read_state_round_trips(tmp_path):
    layout = vault.VaultLayout(tmp_path)
    vault.write_state(layout, {"last_synced_commit": "abc123"})
    assert vault.read_state(layout) == {"last_synced_commit": "abc123"}


def test_write_state_merges_rather_than_overwrites(tmp_path):
    """Both cursors (`last_ingested_commit`, `last_synced_commit`) share this
    one file -- a naive overwrite would erase whichever this call didn't
    mention (spec 2026-09-25-vault-sync-design.md §3)."""
    layout = vault.VaultLayout(tmp_path)
    vault.write_state(layout, {"last_ingested_commit": "old-cursor"})

    vault.write_state(layout, {"last_synced_commit": "new-cursor"})

    assert vault.read_state(layout) == {
        "last_ingested_commit": "old-cursor",
        "last_synced_commit": "new-cursor",
    }


def test_read_state_tolerates_corrupt_json(tmp_path):
    layout = vault.VaultLayout(tmp_path)
    layout.state_json.parent.mkdir(parents=True, exist_ok=True)
    layout.state_json.write_text("{not json", encoding="utf-8")
    assert vault.read_state(layout) == {}
```

Check the top of `test/test_vault.py` already does `from artmind import vault` (it does, per the existing `layout.state_json ==` assertion at line 104) — no new import needed.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault.py -k "read_state or write_state" -v`
Expected: FAIL with `AttributeError: module 'artmind.vault' has no attribute 'read_state'`

- [ ] **Step 3: Implement**

In `artmind/vault.py`, add `import json` to the top-of-file imports (currently just `os`, `dataclasses`, `pathlib`):

```python
import json
import os
from dataclasses import dataclass
from pathlib import Path
```

Then append at the end of the file (after `write_gitignore`):

```python
def read_state(layout: VaultLayout) -> dict:
    """The machine-local cursor file (`state.json`), or `{}` if absent.

    Several independent cursors share this one file -- `last_ingested_commit`
    (documented, not yet implemented) and `last_synced_commit` (`vault
    sync`'s own cursor) -- see `VaultLayout.state_json`'s docstring. Corrupt
    or unreadable JSON is treated as absent rather than raising: a
    hand-edited or partially-written state.json should not brick every
    command that touches it.
    """
    path = layout.state_json
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_state(layout: VaultLayout, updates: dict) -> None:
    """Merge `updates` into `state.json`, preserving every other key already
    there. Multiple cursors coexist in this one file (see `read_state`) --
    a naive whole-file overwrite would silently erase whichever this call
    doesn't mention.
    """
    path = layout.state_json
    state = read_state(layout)
    state.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault.py -k "read_state or write_state" -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add artmind/vault.py test/test_vault.py
git commit -m "feat(vault): add read_state/write_state for the sync cursor"
```

---

### Task 2: `registry.restore_tables` — scoped registry restore

**Files:**
- Modify: `artmind/structured/registry.py` (add `restore_tables`, after `restore_all`)
- Test: `test/test_structured_registry.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_structured_registry.py`:

```python
def test_restore_tables_touches_only_the_requested_table(tmp_path, monkeypatch):
    """A multi-table fixture: restore one table, assert the untouched table's
    rows and ids are byte-identical before/after -- the foreign-key-safety
    property `restore_all` already guarantees for the wholesale case
    (spec 2026-09-25-vault-sync-design.md §7, §12)."""
    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")
    products_id = registry.register_table(
        "default", "products", "banking",
        parquet_path="/tmp/products.parquet", row_count=2, sha256="p1",
    )
    registry.replace_columns(products_id, [{"name": "id", "dtype": "BIGINT", "profile_json": None}])
    registry.upsert_mapping(products_id, "id", "PRODUCT", 0.9, confirmed=True)

    customers_id = registry.register_table(
        "default", "customers", "banking",
        parquet_path="/tmp/customers.parquet", row_count=5, sha256="c1",
    )
    registry.replace_columns(customers_id, [{"name": "id", "dtype": "BIGINT", "profile_json": None}])
    registry.upsert_mapping(customers_id, "id", "CUSTOMER", 0.9, confirmed=True)

    dump = registry.dump_all()
    customers_before = registry.get_table("customers", domain="banking")
    customers_columns_before = registry.get_columns(customers_id)
    customers_mappings_before = registry.list_mappings(customers_id)

    # Mutate products locally (simulating drift since the dump was taken),
    # then restore ONLY products from the dump.
    registry.set_grain(products_id, "lookup", confirmed=False)
    registry.restore_tables(dump, [("banking", "products")])

    restored_products = registry.get_table("products", domain="banking")
    assert restored_products["id"] == products_id
    assert restored_products["grain"] == "instance"  # back to the dumped value

    assert registry.get_table("customers", domain="banking") == customers_before
    assert registry.get_columns(customers_id) == customers_columns_before
    assert registry.list_mappings(customers_id) == customers_mappings_before


def test_restore_tables_raises_when_a_requested_table_is_not_in_the_dump():
    from artmind.structured import registry

    with pytest.raises(ValueError, match="not found in the manifest"):
        registry.restore_tables(
            {"tables": [], "columns": [], "column_mappings": [], "column_roles": [], "datasources": []},
            [("banking", "missing_table")],
        )


def test_restore_tables_inserts_a_brand_new_table_not_previously_registered(tmp_path, monkeypatch):
    """The scoped restore also has to work when the table doesn't exist
    locally at all yet (a fresh query-only host's very first sync)."""
    from artmind.structured import registry

    _patch_db(tmp_path, monkeypatch)
    registry.register_datasource("default", "duckdb", "/tmp/x.duckdb")
    table_id = registry.register_table(
        "default", "products", "banking",
        parquet_path="/tmp/products.parquet", row_count=2, sha256="p1",
    )
    dump = registry.dump_all()
    registry.delete_table(table_id)
    assert registry.get_table("products", domain="banking") is None

    registry.restore_tables(dump, [("banking", "products")])

    restored = registry.get_table("products", domain="banking")
    assert restored is not None
    assert restored["id"] == table_id
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_structured_registry.py -k restore_tables -v`
Expected: FAIL with `AttributeError: module 'artmind.structured.registry' has no attribute 'restore_tables'`

- [ ] **Step 3: Implement**

In `artmind/structured/registry.py`, add after `restore_all` (end of file):

```python
def restore_tables(dump: dict, table_keys: list[tuple[str, str]]) -> None:
    """Scoped sibling of `restore_all`: wipe and reinsert rows for exactly the
    `(domain, table_name)` pairs in `table_keys`, leaving every other
    registered table's rows and ids untouched. `datasources` is left alone
    unless a restored table's own datasource row doesn't currently exist.

    Raises `ValueError` if a requested pair isn't in `dump["tables"]` -- the
    manifest is always a full dump (`export_structured_text`'s own
    docstring), so a requested table missing from it means the diff and the
    manifest have drifted, which should fail loudly rather than silently
    skip the table.

    Same id-preservation contract as `restore_all`, and the same assumption:
    this is for a host whose registry rows, for these tables, were
    themselves always populated by a prior restore (never a real local
    ingest assigning its own autoincrement id) -- `vault sync`'s own use
    case, a query-only host with no other source of truth for these ids.
    """
    dump_rows_by_key = {(r["domain"], r["table_name"]): r for r in dump.get("tables", [])}
    missing = set(table_keys) - dump_rows_by_key.keys()
    if missing:
        raise ValueError(f"table(s) not found in the manifest: {sorted(missing)}")

    conn = _get_db()
    cursor = conn.cursor()
    try:
        for domain, table_name in table_keys:
            existing = cursor.execute(
                'SELECT id FROM "tables" WHERE domain = ? AND table_name = ?',
                (domain, table_name),
            ).fetchone()
            if existing:
                table_id = existing[0]
                cursor.execute("DELETE FROM column_roles WHERE table_id = ?", (table_id,))
                cursor.execute("DELETE FROM column_mappings WHERE table_id = ?", (table_id,))
                cursor.execute('DELETE FROM "columns" WHERE table_id = ?', (table_id,))
                cursor.execute('DELETE FROM "tables" WHERE id = ?', (table_id,))

            row = dump_rows_by_key[(domain, table_name)]
            table_id = row["id"]
            cursor.execute(
                'INSERT INTO "tables" (id, datasource, table_name, domain, source_file, sheet,'
                " parquet_path, version, row_count, refresh_mode, business_key,"
                " effective_date_column, grain, grain_confirmed, grain_status,"
                " bridge_status, mapping_status, ingested_at, sha256)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"], row["datasource"], row["table_name"], row["domain"],
                    row["source_file"], row["sheet"], row["parquet_path"], row["version"],
                    row["row_count"], row["refresh_mode"], row["business_key"],
                    row["effective_date_column"], row.get("grain") or "instance",
                    int(row.get("grain_confirmed") or 0),
                    row.get("grain_status") or "pending",
                    row.get("bridge_status") or "pending",
                    row.get("mapping_status") or "pending",
                    row["ingested_at"], row["sha256"],
                ),
            )

            datasource_exists = cursor.execute(
                "SELECT 1 FROM datasources WHERE name = ?", (row["datasource"],)
            ).fetchone()
            if not datasource_exists:
                ds_row = next(
                    (d for d in dump.get("datasources", []) if d["name"] == row["datasource"]), None
                )
                if ds_row:
                    cursor.execute(
                        "INSERT INTO datasources (name, type, path_or_dsn, created_at) VALUES (?, ?, ?, ?)",
                        (ds_row["name"], ds_row["type"], ds_row["path_or_dsn"], ds_row["created_at"]),
                    )

            for col in dump.get("columns", []):
                if col["table_id"] == table_id:
                    cursor.execute(
                        'INSERT INTO "columns" (table_id, name, dtype, profile_json) VALUES (?, ?, ?, ?)',
                        (col["table_id"], col["name"], col["dtype"], col["profile_json"]),
                    )
            for m in dump.get("column_mappings", []):
                if m["table_id"] == table_id:
                    cursor.execute(
                        'INSERT INTO column_mappings (table_id, "column", entity_class, confirmed,'
                        " confidence, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (m["table_id"], m["column"], m["entity_class"], m["confirmed"], m["confidence"], m["updated_at"]),
                    )
            for r in dump.get("column_roles", []):
                if r["table_id"] == table_id:
                    cursor.execute(
                        'INSERT INTO column_roles (table_id, "column", bridge_role, confirmed,'
                        " confidence, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (r["table_id"], r["column"], r["bridge_role"], r["confirmed"], r["confidence"], r["updated_at"]),
                    )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_structured_registry.py -k restore_tables -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full registry test file for regressions**

Run: `uv run --group dev pytest test/test_structured_registry.py -v`
Expected: PASS (all, including pre-existing tests)

- [ ] **Step 6: Commit**

```bash
git add artmind/structured/registry.py test/test_structured_registry.py
git commit -m "feat(structured): add registry.restore_tables for scoped restore"
```

---

### Task 3: Scoped `import_structured_text(tables=...)`

**Files:**
- Modify: `artmind/structured/text_export.py`
- Test: `test/test_structured_text_export.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_structured_text_export.py`:

```python
def test_import_structured_text_scoped_to_tables_leaves_others_untouched(tmp_path, monkeypatch):
    """`tables=[(domain, table_name), ...]` rebuilds only the requested
    tables' parquet and registry rows -- never `shutil.rmtree`s the whole
    STRUCTURED_DIR (spec 2026-09-25-vault-sync-design.md §7)."""
    _patch_stores(tmp_path, monkeypatch)
    import paths
    from artmind.structured import registry
    from artmind.structured.duckdb_adapter import DuckDBDatasource
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text, import_structured_text

    products_csv = tmp_path / "products.csv"
    _write_csv(products_csv, [["id", "name"], [1, "Widget"], [2, "Gadget"]])
    ingest_structured_file(products_csv, "banking")

    customers_csv = tmp_path / "customers.csv"
    _write_csv(customers_csv, [["id", "name"], [1, "Acme"]])
    ingest_structured_file(customers_csv, "banking")

    export_structured_text()

    customers_parquet = paths.STRUCTURED_DIR / "banking" / "customers.parquet"
    customers_mtime_before = customers_parquet.stat().st_mtime_ns

    summary = import_structured_text(tables=[("banking", "products")])

    assert summary["tables_loaded"] == 1
    assert registry.get_table("products", domain="banking") is not None
    assert registry.get_table("customers", domain="banking") is not None, (
        "a scoped restore must never wipe an untouched table's registry row"
    )
    assert customers_parquet.stat().st_mtime_ns == customers_mtime_before, (
        "a scoped restore must never rewrite an untouched table's parquet"
    )

    ds = DuckDBDatasource()
    ds.ensure_views(registry.list_tables())
    rows = ds.run_sql("SELECT * FROM products ORDER BY id")
    assert rows == [{"id": 1, "name": "Widget"}, {"id": 2, "name": "Gadget"}]


def test_import_structured_text_unscoped_behaviour_is_unchanged(tmp_path, monkeypatch):
    """Regression pin: `tables=None` (the default) is byte-for-byte today's
    existing wholesale behavior."""
    _patch_stores(tmp_path, monkeypatch)
    import shutil

    import paths
    from artmind.structured import registry
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text, import_structured_text

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"]])
    ingest_structured_file(csv_path, "banking")
    export_structured_text()

    shutil.rmtree(paths.STRUCTURED_DIR)
    import artmind.db as db
    db.DB_PATH.unlink(missing_ok=True)
    db._init_db()

    summary = import_structured_text()

    assert summary["tables_loaded"] == 1
    assert registry.get_table("products", domain="banking") is not None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_structured_text_export.py -k "scoped_to_tables or unscoped_behaviour" -v`
Expected: FAIL with `TypeError: import_structured_text() got an unexpected keyword argument 'tables'`

- [ ] **Step 3: Implement**

In `artmind/structured/text_export.py`, replace the `import_structured_text` function:

```python
def import_structured_text(
    src_dir: Path | None = None, *, tables: list[tuple[str, str]] | None = None
) -> dict:
    """Wipe and rebuild the structured store from `src_dir`'s CSV + `manifest.json`.

    `tables=None` (default) is today's existing wholesale behavior, byte for
    byte: every registered table's parquet + registry rows are wiped and
    rebuilt. `tables=[(domain, table_name), ...]` scopes this to exactly
    those tables -- `vault sync`'s track B, which must never pay for (or
    disturb) a table its diff didn't touch. Scoped restore never
    `shutil.rmtree`s STRUCTURED_DIR; only the requested tables' parquet is
    rewritten, and `registry.restore_tables` (not `restore_all`) leaves every
    other table's registry rows untouched.

    Each CSV's own header row -- not the manifest's `columns` ordering, which
    SQLite gives no guarantee of preserving across a dump/restore -- decides
    the column order DuckDB's `columns={...}` schema map is built in:
    `read_csv` matches that map to the file *positionally*, not by name, so a
    mismatch would silently read every column as the wrong type instead of
    raising.
    """
    src_dir = Path(src_dir) if src_dir else paths.STRUCTURED_TEXT_DIR
    manifest_path = src_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"No structured text export found at {src_dir} (missing {MANIFEST_NAME})")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    dtypes_by_table: dict[int, dict[str, str]] = {}
    for col in manifest.get("columns", []):
        dtypes_by_table.setdefault(col["table_id"], {})[col["name"]] = col["dtype"]

    target_rows = manifest.get("tables", [])
    if tables is not None:
        wanted = set(tables)
        target_rows = [r for r in target_rows if (r["domain"], r["table_name"]) in wanted]

    # Never trust the path recorded when the manifest was written -- it's
    # meaningless once this vault has been cloned onto a different machine.
    for row in target_rows:
        row["parquet_path"] = str(parquet_path_for(row["domain"], row["table_name"]))

    if tables is None:
        if paths.STRUCTURED_DIR.exists():
            shutil.rmtree(paths.STRUCTURED_DIR)
        paths.STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)
        registry.restore_all(manifest)
    else:
        paths.STRUCTURED_DIR.mkdir(parents=True, exist_ok=True)
        registry.restore_tables(manifest, tables)

    ds = DuckDBDatasource()
    loaded = 0
    skipped: list[str] = []
    for table in target_rows:
        csv_path = _table_csv_path(src_dir, table["domain"], table["table_name"])
        if not csv_path.is_file():
            logger.warning(
                "structured text import: {!r} has no CSV at {}, skipping",
                table["table_name"], csv_path,
            )
            skipped.append(table["table_name"])
            continue
        header = _csv_header(csv_path)
        col_dtypes = _SYSTEM_COLUMN_DTYPES | dtypes_by_table.get(table["id"], {})
        missing = [c for c in header if c not in col_dtypes]
        if missing:
            raise ValueError(
                f"{csv_path}: column(s) {missing} have no recorded dtype in {MANIFEST_NAME} "
                f"for table {table['table_name']!r}"
            )
        columns_map = ", ".join(
            f"{_quote_literal(name)}: {_quote_literal(col_dtypes[name])}" for name in header
        )
        parquet_path = Path(table["parquet_path"])
        parquet_path.parent.mkdir(parents=True, exist_ok=True)
        ds.con.execute(
            f"COPY (SELECT * FROM read_csv({_quote_literal(str(csv_path))}, header=true, "
            f"nullstr={_quote_literal(NULL_SENTINEL)}, columns={{{columns_map}}})) "
            f"TO {_quote_literal(str(parquet_path))} (FORMAT PARQUET)"
        )
        loaded += 1

    ds.ensure_views(registry.list_tables())

    logger.info("structured text import: {} table(s) restored from {}", loaded, src_dir)
    return {
        "source": str(src_dir),
        "tables_in_manifest": len(manifest.get("tables", [])),
        "tables_loaded": loaded,
        "skipped_no_csv": skipped,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_structured_text_export.py -v`
Expected: PASS (all, including pre-existing tests — this is the regression check for the `tables=None` pin)

- [ ] **Step 5: Commit**

```bash
git add artmind/structured/text_export.py test/test_structured_text_export.py
git commit -m "feat(structured): scope import_structured_text to specific tables"
```

---

### Task 4: `db restore-text --table` CLI option

**Files:**
- Modify: `artmind/cli.py` (`db_restore_text`, around line 2133)
- Test: `test/test_structured_text_export.py`

- [ ] **Step 1: Write the failing test**

Append to `test/test_structured_text_export.py`:

```python
def test_db_restore_text_cli_table_option_scopes_the_restore(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from click.testing import CliRunner

    from artmind.cli import cli
    from artmind.structured import registry
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text

    products_csv = tmp_path / "products.csv"
    _write_csv(products_csv, [["id", "name"], [1, "Widget"]])
    ingest_structured_file(products_csv, "banking")
    customers_csv = tmp_path / "customers.csv"
    _write_csv(customers_csv, [["id", "name"], [1, "Acme"]])
    ingest_structured_file(customers_csv, "banking")
    export_structured_text()

    products_id_before = registry.get_table("products", domain="banking")["id"]
    registry.set_grain(products_id_before, "lookup", confirmed=False)

    result = CliRunner().invoke(
        cli, ["db", "restore-text", "--confirm", "--table", "products", "--compact"]
    )

    assert result.exit_code == 0, result.output
    assert registry.get_table("products", domain="banking")["grain"] == "instance"
    assert registry.get_table("customers", domain="banking") is not None


def test_db_restore_text_cli_table_option_is_comma_splittable(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from click.testing import CliRunner

    from artmind.cli import cli
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured.text_export import export_structured_text

    for name in ("products", "customers"):
        csv_path = tmp_path / f"{name}.csv"
        _write_csv(csv_path, [["id"], [1]])
        ingest_structured_file(csv_path, "banking")
    export_structured_text()

    result = CliRunner().invoke(
        cli, ["db", "restore-text", "--confirm", "--table", "products,customers", "--compact"]
    )

    assert result.exit_code == 0, result.output
    import json as jsonlib
    assert jsonlib.loads(result.output)["tables_loaded"] == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_structured_text_export.py -k table_option -v`
Expected: FAIL with `Error: No such option: --table`

- [ ] **Step 3: Implement**

In `artmind/cli.py`, add `_parse_tables` right after the existing `_parse_domains` helper (around line 90):

```python
def _parse_tables(values: "tuple[str, ...]") -> list[str]:
    """Flatten repeatable/comma-split --table values into a deduped list,
    mirroring `_parse_domains`'s own convention for --domain."""
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in value.split(","):
            name = part.strip()
            if name and name not in seen:
                seen.add(name)
                out.append(name)
    return out
```

Then replace the `db_restore_text` command (around line 2133):

```python
@db.command("restore-text")
@click.argument("path", required=False, type=click.Path(exists=True))
@click.option("--confirm", is_flag=True, help="Required — wipes the current structured store")
@click.option(
    "--table", "table", multiple=True,
    help="Table(s) to restore (repeatable; comma-splittable). Default: every table in the export "
    "(today's wholesale behavior, unchanged). Pairable with --domain for disambiguation, like `db refresh`.",
)
@click.option("--domain", "domain", multiple=True, help="Domain(s) to scope --table resolution (repeatable; comma-splittable).")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def db_restore_text(path, confirm, table, domain, compact):
    """Wipe and rebuild the structured store from a `db export-text` directory (default: the vault's structured_text dir).

    The parquet + DuckDB catalog are a rebuildable cache (`docs/vault.md`) —
    this is the command a fresh `git clone` of a vault runs to regenerate
    them from the committed CSV + manifest.json. Pass --table to restore only
    specific tables (used internally by `vault sync`'s track B) rather than
    the whole store.
    """
    if not confirm:
        raise click.ClickException("pass --confirm — restoring wipes the current structured store")
    table_keys: list[tuple[str, str]] | None = None
    if table:
        table_keys = []
        for name in _parse_tables(table):
            row = _resolve_table_row(name, domain)
            table_keys.append((row["domain"], row["table_name"]))
    try:
        summary = import_structured_text(Path(path) if path else None, tables=table_keys)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _echo_json(summary, compact)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_structured_text_export.py -k table_option -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the CLI guide coverage test for regressions**

Run: `uv run --group dev pytest test/test_cli_guide.py -v`
Expected: PASS (an added option on an existing command doesn't change routing)

- [ ] **Step 6: Commit**

```bash
git add artmind/cli.py test/test_structured_text_export.py
git commit -m "feat(cli): add --table to db restore-text for scoped restore"
```

---

### Task 5: `ingest.retract_document`

**Files:**
- Modify: `artmind/ingest.py` (add `retract_document`, after `_retract_prior_version` or near `commit_to_graph`)
- Test: `test/test_reingest.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_reingest.py`. First extend `_classify` (near the top) to recognize the new Document relabel query — add one more `if` branch:

```python
def _classify(cypher: str) -> str:
    if "REMOVE o:Observation SET o:ObservationHistory" in cypher:
        return "demote_observations"
    if "REMOVE c:DocChunk SET c:DocChunkHistory" in cypher:
        return "relabel_chunks"
    if "REMOVE d:Document SET d:DocumentHistory" in cypher:
        return "relabel_document"
    if "DETACH DELETE c" in cypher:
        return "chunk_delete"
    if "DETACH DELETE e" in cypher:
        return "entity_delete"
    return "other"
```

Then append the new tests at the end of the file:

```python
# ── retract_document (vault sync §8: demote and write nothing new) ─────────


def test_retract_document_demotes_document_chunks_and_observations(monkeypatch):
    session = _RetractSession(counts={"demote_observations": 2, "relabel_chunks": 2})
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)

    import artmind.projection as projection
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id, **kw: {("Acme", "ORG", "banking")})

    result = ing.retract_document("docA", "banking")

    assert session.ran("relabel_document")
    cypher, kw = session.call("relabel_document")
    assert kw["doc_id"] == "docA"
    assert "DELETE" not in cypher, "the Document node is relabelled, never deleted"
    assert result["affected_keys"] == [("Acme", "ORG", "banking")]
    assert result["observations_demoted"] == 2
    assert result["chunks"] == 2
    assert result["doc_id"] == "docA"


def test_retract_document_captures_keys_before_demoting(monkeypatch):
    """Mirrors `_commit_document_tx`'s own ordering: a rename between versions
    strands the old key if it's read after the demotion instead of before."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)

    import artmind.projection as projection
    call_order: list[str] = []
    monkeypatch.setattr(
        projection, "keys_for_document",
        lambda tx, doc_id, **kw: call_order.append("keys_for_document") or set(),
    )
    original_retract = ing._retract_prior_version

    def _wrapped_retract(tx, domain, doc_id):
        call_order.append("_retract_prior_version")
        return original_retract(tx, domain, doc_id)

    monkeypatch.setattr(ing, "_retract_prior_version", _wrapped_retract)

    ing.retract_document("docA", "banking")

    assert call_order == ["keys_for_document", "_retract_prior_version"]


def test_retract_document_never_rebuilds_the_projection_itself(monkeypatch):
    """The rebuild is deferred to the caller (vault sync), which unions this
    document's affected_keys with every other change in the same run before
    a single batched rebuild — exactly like commit_to_graph(...,
    defer_rebuild=True)'s own deferred_keys."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)

    import artmind.projection as projection
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id, **kw: set())
    rebuild_called = []
    monkeypatch.setattr(projection, "rebuild", lambda *a, **k: rebuild_called.append(1) or {})

    ing.retract_document("docA", "banking")

    assert rebuild_called == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_reingest.py -k retract_document -v`
Expected: FAIL with `AttributeError: module 'artmind.ingest' has no attribute 'retract_document'`

- [ ] **Step 3: Implement**

In `artmind/ingest.py`, add after `_retract_prior_version` (around line 1620, before `kg_work_was_done`):

```python
def retract_document(doc_id: str, domain: str) -> dict:
    """Demote one document's own `:Document`/`:DocChunk`/`:Observation` nodes
    to their History labels, and nothing else -- the standalone entry point
    `_retract_prior_version` never had, for a document whose KG-staging
    folder disappeared with no replacement version to write (`vault sync`'s
    retraction path).

    Reuses `_retract_prior_version`'s exact relabeling for observations/chunks
    -- the same primitive `_commit_document_tx` calls before writing a new
    version -- plus one more relabel for the `:Document` node itself, which
    `_retract_prior_version` deliberately leaves alone (an ordinary re-ingest
    revives it under `:Document` again two steps later; this call has no
    next step to revive it in).

    Deliberately does NOT run the projection rebuild: the caller unions
    `affected_keys` across every change in one run before a single batched
    rebuild, exactly like `commit_to_graph(..., defer_rebuild=True)`'s own
    `deferred_keys`. `rebuild_key`'s existing zero-observations GC path
    already handles what happens next once that rebuild runs -- no new
    projection logic, only a new way to reach it without a replacement
    document.
    """
    from artmind import projection
    from artmind.graph_query import neo4j_session

    def _tx(tx):
        keys = projection.keys_for_document(tx, doc_id)
        retracted = _retract_prior_version(tx, domain, doc_id)
        tx.run(
            "MATCH (d:Document {id: $doc_id}) REMOVE d:Document SET d:DocumentHistory",
            doc_id=doc_id,
        )
        return {"doc_id": doc_id, "domain": domain, "affected_keys": sorted(keys), **retracted}

    with neo4j_session() as session:
        result = session.execute_write(_tx)
    logger.info(
        "Retracted {} ({}): {} observation(s), {} chunk(s) demoted to history",
        doc_id, domain, result["observations_demoted"], result["chunks"],
    )
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_reingest.py -v`
Expected: PASS (all, including pre-existing tests)

- [ ] **Step 5: Commit**

```bash
git add artmind/ingest.py test/test_reingest.py
git commit -m "feat(ingest): add retract_document, a standalone demote-with-no-replacement primitive"
```

---

### Task 6: `table2graph.table_to_graph(..., defer_rebuild=...)`

**Files:**
- Modify: `artmind/table2graph.py` (`table_to_graph`, around line 1282)
- Test: `test/test_table2graph.py`

- [ ] **Step 1: Read how the existing CLI-level tests mock the Neo4j write in `test/test_table2graph.py`**

Find the existing test(s) that call `t2g.table_to_graph(...)` end-to-end (they patch `ingest._write_to_neo4j` and `t2g._rebuild_in_batches`, per the file's own module docstring: "the CLI tests patch the table read and the Neo4j commit"). Match that exact patching style for the new tests below.

- [ ] **Step 2: Write the failing tests**

Append to `test/test_table2graph.py` (adjust patch targets/fixture names to match whatever helper the existing table_to_graph tests already use for `rows`/`table`/`mapping`/`schema` — use the same `SCHEMA`/`MAPPING` fixtures already defined at the top of this file):

```python
def test_table_to_graph_defer_rebuild_skips_its_own_rebuild_and_sweeps(tmp_path, monkeypatch):
    """§5 step 2 of the vault-sync spec: `defer_rebuild=True` must return the
    affected keys for the CALLER to batch-rebuild later, not rebuild/sweep
    internally like the ordinary (CLI) path does."""
    monkeypatch.chdir(tmp_path)
    mapping = t2g.parse_mapping(MAPPING)
    table = {"domain": "perf", "table_name": "hr_export_1", "id": 1}

    monkeypatch.setattr(t2g, "read_table_rows", lambda table, as_of=None: ([
        {"TalentID": "1", "Employee Name": "Doe, Jane", "Band": "B3", "Mgr?": "N"},
    ], ["TalentID", "Employee Name", "Band", "Mgr?"]))

    import artmind.ingest as ing
    fake_summary = {
        "chunks": 1, "observations": 1, "relationships": 0, "retracted": {},
        "deferred_keys": [("Jane Doe (1)", "PERSON", "perf")],
        "unembedded_chunk_ids": [],
    }
    monkeypatch.setattr(
        ing, "_write_to_neo4j",
        lambda directory, domain, defer_rebuild=False: fake_summary,
    )
    rebuild_called = []
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys: rebuild_called.append(keys) or {})
    sweep_called = []
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: sweep_called.append(keys) or 0)

    report = t2g.table_to_graph(table, mapping, schema=SCHEMA, embed=True, defer_rebuild=True)

    assert rebuild_called == [], "the rebuild must be deferred to the caller"
    assert sweep_called == [], "the embed sweep must be deferred to the caller too"
    assert report["commit"]["deferred_keys"] == [("Jane Doe (1)", "PERSON", "perf")]
    assert report["commit"]["projection"] == {"deferred": True}


def test_table_to_graph_default_behaviour_still_rebuilds_immediately(tmp_path, monkeypatch):
    """Regression pin: `defer_rebuild=False` (the default) is byte-for-byte
    today's existing CLI behavior."""
    monkeypatch.chdir(tmp_path)
    mapping = t2g.parse_mapping(MAPPING)
    table = {"domain": "perf", "table_name": "hr_export_1", "id": 1}

    monkeypatch.setattr(t2g, "read_table_rows", lambda table, as_of=None: ([
        {"TalentID": "1", "Employee Name": "Doe, Jane", "Band": "B3", "Mgr?": "N"},
    ], ["TalentID", "Employee Name", "Band", "Mgr?"]))

    import artmind.ingest as ing
    fake_summary = {
        "chunks": 1, "observations": 1, "relationships": 0, "retracted": {},
        "deferred_keys": [("Jane Doe (1)", "PERSON", "perf")],
        "unembedded_chunk_ids": [],
    }
    monkeypatch.setattr(
        ing, "_write_to_neo4j",
        lambda directory, domain, defer_rebuild=False: fake_summary,
    )
    rebuild_called = []
    monkeypatch.setattr(t2g, "_rebuild_in_batches", lambda keys: rebuild_called.append(keys) or {"rebuilt": 1})

    report = t2g.table_to_graph(table, mapping, schema=SCHEMA, embed=False, defer_rebuild=False)

    assert rebuild_called == [[("Jane Doe (1)", "PERSON", "perf")]]
    assert "deferred_keys" not in report["commit"]
    assert report["commit"]["projection"] == {"rebuilt": 1}
```

If this file's existing tests use a different mocking convention for `read_table_rows`/`ingest._write_to_neo4j` (check first), adapt the two new tests to match it exactly rather than introducing a second style in the same file.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_table2graph.py -k defer_rebuild -v`
Expected: FAIL with `TypeError: table_to_graph() got an unexpected keyword argument 'defer_rebuild'`

- [ ] **Step 4: Implement**

In `artmind/table2graph.py`, modify `table_to_graph`'s signature and the tail of its body (around line 1282-1343):

```python
def table_to_graph(
    table: dict,
    mapping: TableMapping,
    *,
    schema: dict,
    as_of: str | None = None,
    dry_run: bool = False,
    embed: bool = True,
    defer_rebuild: bool = False,
) -> dict:
    """Validate, build, stage and commit one table. Returns the report.

    Raises `MappingError` when the mapping fails validation -- nothing is
    written. A dry run validates and builds (so its counts are real) but
    stages and commits nothing.

    `defer_rebuild=True` skips this call's own projection rebuild and embed
    sweeps entirely, returning the affected keys in
    `report["commit"]["deferred_keys"]` instead of rebuilding them here --
    `vault sync`'s track B, which unions them with every other change in the
    same run before a single batched rebuild (spec
    2026-09-25-vault-sync-design.md §5). The ordinary CLI path
    (`defer_rebuild=False`, the default) is unchanged.
    """
    from artmind import ingest

    if mapping.domain and mapping.domain != table["domain"]:
        raise MappingError(
            f"{mapping.path}: mapping is for domain {mapping.domain!r}, "
            f"table {table['table_name']!r} is in {table['domain']!r}"
        )
    rows, columns = read_table_rows(table, as_of)
    errors, warnings = validate_mapping(mapping, schema, columns)
    if errors:
        raise MappingError(
            f"{mapping.path or 'mapping'} is invalid for table {table['table_name']!r}:\n  - "
            + "\n  - ".join(errors)
        )
    staged, report = build_staged(table, rows, columns, mapping, schema)
    report["warnings"] = warnings
    report["as_of"] = as_of
    report["dry_run"] = dry_run
    if dry_run:
        return report

    directory = stage_dir(table["domain"], table["table_name"])
    write_staged(staged, report, directory)
    report["staged_at"] = str(directory)
    # Phase 1: the document transaction, rebuild deferred. Phase 2: the
    # rebuild, in batches (see `_rebuild_in_batches` for why not one) --
    # unless the CALLER (`vault sync`) wants to defer that too.
    summary = ingest._write_to_neo4j(directory, table["domain"], defer_rebuild=True)
    if summary is None:
        raise RuntimeError(f"could not read the staged files back from {directory}")
    keys = summary.get("deferred_keys") or []
    report["commit"] = {
        "chunks": summary.get("chunks"),
        "observations": summary.get("observations"),
        "relationships": summary.get("relationships"),
        "retracted": summary.get("retracted"),
        "affected_keys": len(keys),
    }
    if defer_rebuild:
        report["commit"]["deferred_keys"] = sorted(keys)
        report["commit"]["projection"] = {"deferred": True}
    else:
        report["commit"]["projection"] = _rebuild_in_batches(keys)
        if embed:
            report["commit"]["entities_embedded"] = ingest._sweep_embeddings(table["domain"], keys)
            report["commit"]["chunks_embedded"] = ingest._sweep_chunk_embeddings(
                summary.get("unembedded_chunk_ids") or []
            )
    logger.info(
        "table2graph: {} -> {} observation(s), {} relationship(s), {} chunk(s)",
        table["table_name"], report["observations"], report["relationship_observations"], report["chunks"],
    )
    return report
```

(Only the signature line, the new docstring paragraph, and the `if defer_rebuild: ... else: ...` block at the tail actually change — everything else is copied unchanged from the current implementation for context.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_table2graph.py -v`
Expected: PASS (all, including pre-existing tests)

- [ ] **Step 6: Commit**

```bash
git add artmind/table2graph.py test/test_table2graph.py
git commit -m "feat(table2graph): add defer_rebuild to skip the internal rebuild/sweep"
```

---

### Task 7: `vault_sync.py` — git diff/show primitives

**Files:**
- Create: `artmind/vault_sync.py`
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

Create `test/test_vault_sync.py`:

```python
"""`vault sync`: git-diff-driven, CDC-style replay (spec
docs/superpowers/specs/2026-09-25-vault-sync-design.md)."""
import subprocess

import pytest

import artmind.vault_sync as vs


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _commit_all(path, message):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=path, check=True)


@pytest.fixture()
def repo(tmp_path):
    _init_git_repo(tmp_path)
    return tmp_path


def test_head_sha_returns_the_current_commit(repo):
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")

    sha = vs.head_sha(repo)

    log = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert sha == log


def test_head_sha_raises_a_clear_error_when_the_repo_has_no_commits_yet(repo):
    """Exactly the state `artmind init` leaves a fresh vault in (`git init`,
    never a commit) -- must raise a specific, actionable VaultSyncError, not
    a generic git-command-failed message."""
    with pytest.raises(vs.VaultSyncError, match="no commits yet"):
        vs.head_sha(repo)


def test_diff_name_status_reports_added_files(repo):
    scope = repo / "sub"
    scope.mkdir()
    (scope / "f.txt").write_text("x")
    _commit_all(repo, "first")

    rows = vs._diff_name_status(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo), scope)

    assert rows == [("A", "sub/f.txt")]


def test_diff_name_status_reports_removed_files(repo):
    scope = repo / "sub"
    scope.mkdir()
    (scope / "f.txt").write_text("x")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)
    (scope / "f.txt").unlink()
    _commit_all(repo, "second")

    rows = vs._diff_name_status(repo, base, vs.head_sha(repo), scope)

    assert rows == [("D", "sub/f.txt")]


def test_diff_name_status_reports_modified_files(repo):
    scope = repo / "sub"
    scope.mkdir()
    (scope / "f.txt").write_text("x")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)
    (scope / "f.txt").write_text("y")
    _commit_all(repo, "second")

    rows = vs._diff_name_status(repo, base, vs.head_sha(repo), scope)

    assert rows == [("M", "sub/f.txt")]


def test_show_returns_none_for_a_path_absent_at_that_revision(repo):
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)

    assert vs._show(repo, base, "never_existed.txt") is None


def test_show_returns_the_content_at_that_revision(repo):
    (repo / "a.txt").write_text("hello\n")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)
    (repo / "a.txt").write_text("changed\n")
    _commit_all(repo, "second")

    assert vs._show(repo, base, "a.txt") == "hello\n"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'artmind.vault_sync'`

- [ ] **Step 3: Implement**

Create `artmind/vault_sync.py`:

```python
"""`vault sync`: git-diff-driven, CDC-style replay into Neo4j and the
structured store (docs/superpowers/specs/2026-09-25-vault-sync-design.md).

Detects exactly which committed KG-staging document folders
(`.artmind/data/kg/**`) and committed structured-store text
(`.artmind/data/structured_text/**`) changed since the last sync, and
replays only those changes -- incremental and git-native, complementing
(not replacing) the whole-graph `session close`/`session initiate` snapshot.
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from utils.functions import run_command

#: The git object id of the canonical empty tree, present in every
#: repository -- `git diff <this> HEAD` is the standard way to diff "against
#: nothing", which is exactly `--bootstrapEmpty`'s "everything currently
#: committed counts as added" semantics, without special-casing a repo's own
#: (possibly multiple) root commit(s).
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class VaultSyncError(Exception):
    """A `vault sync` run cannot proceed -- refused before writing anything."""


def _git(vault_dir: Path, args: str) -> str:
    rc, out, err = run_command(f"git {args}", cwd=vault_dir)
    if rc != 0:
        raise VaultSyncError(f"git {args} failed: {err or out}")
    return out


def head_sha(vault_dir: Path) -> str:
    """The vault's current commit sha. Raises a clear, specific
    `VaultSyncError` (not `_git`'s generic wrapper) when there are no
    commits yet -- exactly the state `artmind init` leaves a fresh vault in
    (it runs `git init`, never a commit), and the one case every `sync()`
    call needs a real, named message for, since bootstrap or not, dry-run or
    not, there is nothing to sync/stamp without a HEAD to point at."""
    rc, out, err = run_command("git rev-parse HEAD", cwd=vault_dir, expected_codes=(128,))
    if rc != 0:
        raise VaultSyncError(
            "this vault's git repo has no commits yet -- nothing to sync. "
            "Commit something to it first, then run `vault sync` again."
        )
    return out.strip()


def _diff_name_status(vault_dir: Path, base: str, head: str, scope: Path) -> list[tuple[str, str]]:
    """`[(status, path), ...]` for `scope` between `base` and `head`, paths
    relative to `vault_dir`. `status` is git's own single-letter code
    (A/M/D); `--no-renames` guarantees a delete+add pair rather than a
    single "R100" line regardless of the user's global git config, which
    the per-path classification in `_classify_kg_diff`/
    `_classify_structured_text_diff` depends on."""
    rel_scope = scope.relative_to(vault_dir)
    out = _git(vault_dir, f'diff --no-renames --name-status {base} {head} -- "{rel_scope}"')
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        status, _, path = line.partition("\t")
        rows.append((status[0], path))
    return rows


def _show(vault_dir: Path, rev: str, relpath: str) -> str | None:
    """`git show rev:relpath`'s content, or None if that path doesn't exist
    at `rev` -- reading a removed KG-staging folder's document.json from
    before it was removed, the only way to recover its `doc_id`."""
    rc, out, _ = run_command(f'show "{rev}:{relpath}"', cwd=vault_dir, expected_codes=(128,))
    return out if rc == 0 else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "feat(vault): add vault_sync module with git diff/show primitives"
```

---

### Task 8: Diff classification (track A + track B)

**Files:**
- Modify: `artmind/vault_sync.py` (add `SyncPlan`, `classify_diff`, and the two `_classify_*` helpers)
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault_sync.py`:

```python
# ── classify_diff: track A (.artmind/data/kg/**) ────────────────────────────


def _write_doc_folder(kg_dir, domain, docdir, doc_id, extra_files=("chunks.json", "relationships.json")):
    folder = kg_dir / domain / docdir
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "document.json").write_text(f'{{"id": "{doc_id}"}}')
    (folder / "observations.json").write_text("[]")
    for name in extra_files:
        (folder / name).write_text("[]")


def _patch_kg_dir(monkeypatch, vault_dir):
    import paths
    kg_dir = vault_dir / ".artmind" / "data" / "kg"
    monkeypatch.setattr(paths, "KG_DIR", kg_dir)
    return kg_dir


def _patch_structured_text_dir(monkeypatch, vault_dir):
    import paths
    st_dir = vault_dir / ".artmind" / "data" / "structured_text"
    monkeypatch.setattr(paths, "STRUCTURED_TEXT_DIR", st_dir)
    return st_dir


def test_classify_diff_replays_an_added_document_folder(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    from artmind.vault import VaultLayout

    plan = vs.classify_diff(repo, VaultLayout(repo), vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "doc1")]
    assert plan.retract == []


def test_classify_diff_replays_a_modified_document_folder(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    (kg_dir / "banking" / "doc1" / "observations.json").write_text('[{"key": "x"}]')
    _commit_all(repo, "edit doc1")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.replay_docs == [("banking", "doc1")]


def test_classify_diff_retracts_a_removed_document_folder(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "doc1")
    _commit_all(repo, "remove doc1")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.replay_docs == []
    assert plan.retract == [("banking", "docid-1")]


def test_classify_diff_retracts_a_removed_table_folder_directly(repo, monkeypatch):
    """A table__<name> folder removed directly (out-of-band, not via a
    structured-text change) uses the identical retraction path (spec §4)."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "table__accounts", "table:banking:accounts")
    _commit_all(repo, "add table")
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "table__accounts")
    _commit_all(repo, "remove table folder")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.retract == [("banking", "table:banking:accounts")]


def test_classify_diff_ignores_a_folder_with_no_observations_json_change(repo, monkeypatch):
    """Only some OTHER file (e.g. table2graph_report.json) changed --
    neither replay nor retract triggers (spec §4's own conditioning on
    observations.json specifically)."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1", extra_files=("chunks.json", "table2graph_report.json"))
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    (kg_dir / "banking" / "doc1" / "table2graph_report.json").write_text('{"n": 1}')
    _commit_all(repo, "edit report only")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.replay_docs == []
    assert plan.retract == []


def test_classify_diff_scopes_to_requested_domains(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _write_doc_folder(kg_dir, "legal", "doc2", "docid-2")
    _commit_all(repo, "add both")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), vs.EMPTY_TREE_SHA, vs.head_sha(repo), domains=["banking"])

    assert plan.replay_docs == [("banking", "doc1")]


# ── classify_diff: track B (.artmind/data/structured_text/**) ───────────────


def test_classify_diff_regenerates_an_added_table_csv(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add table text")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), vs.EMPTY_TREE_SHA, vs.head_sha(repo))

    assert plan.regenerate_tables == [("banking", "accounts")]


def test_classify_diff_retracts_a_removed_table_csv(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add table text")
    base = vs.head_sha(repo)
    (st_dir / "banking" / "accounts.csv").unlink()
    _commit_all(repo, "remove table text")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.regenerate_tables == []
    assert plan.retract == [("banking", "table:banking:accounts")]


def test_classify_diff_manifest_only_change_triggers_nothing(repo, monkeypatch):
    """Documented scoping decision (not an oversight): manifest.json is a
    single shared file, not "under a given table's export" -- a
    manifest-only change with no accompanying CSV diff regenerates nothing
    in this version."""
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add table text")
    base = vs.head_sha(repo)
    (st_dir / "manifest.json").write_text('{"changed": true}')
    _commit_all(repo, "edit manifest only")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.regenerate_tables == []


def test_classify_diff_dedupes_a_table_retracted_from_both_tracks(repo, monkeypatch):
    """If a table__* folder AND its structured-text CSV are both removed in
    the same diff, the table is retracted once, not twice."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "table__accounts", "table:banking:accounts")
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add both")
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "table__accounts")
    (st_dir / "banking" / "accounts.csv").unlink()
    _commit_all(repo, "remove both")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, vs.head_sha(repo))

    assert plan.retract == [("banking", "table:banking:accounts")]


def test_classify_diff_never_sees_track_bs_uncommitted_output_in_the_same_run(repo, monkeypatch):
    """§5's core sequencing correctness property (spec §12's second test
    case): `classify_diff` reads committed git history only (`git diff`
    between two fixed revisions) -- it is structurally incapable of seeing
    files track B has written to the working tree but not yet committed,
    regardless of when in a `sync()` run it's called. This test proves the
    property directly: write a FRESH, uncommitted table__* folder (exactly
    what track B's regenerate step produces mid-run -- see `sync()`), and
    confirm classify_diff's track-A pass does not report it as an added
    document folder -- an uncommitted file simply cannot appear in any
    `base..head` diff."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)
    head = vs.head_sha(repo)  # no new commit -- this run's diff_range is fixed and empty

    # Simulate track B having just written fresh output mid-run, uncommitted.
    _write_doc_folder(kg_dir, "banking", "table__accounts", "table:banking:accounts")

    from artmind.vault import VaultLayout
    plan = vs.classify_diff(repo, VaultLayout(repo), base, head)

    assert plan.replay_docs == [], (
        "an uncommitted table__* folder must never be picked up as a track-A "
        "replay within the same run that just wrote it"
    )


def test_classify_diff_raises_if_removed_folder_never_existed_at_base(monkeypatch, tmp_path):
    """Defensive: should be unreachable in practice -- git cannot report a
    `D` status for a path that was absent at BOTH diff endpoints, so this
    monkeypatches `_diff_name_status`/`_show` directly to simulate the
    impossible case, rather than trying (and failing) to construct it via
    real git commands. Fail loud rather than silently guess if it ever
    somehow occurs."""
    from artmind.vault import VaultLayout

    monkeypatch.setattr(vs, "_diff_name_status", lambda *a, **k: [("D", "banking/doc1/observations.json")])
    monkeypatch.setattr(vs, "_show", lambda *a, **k: None)

    with pytest.raises(vs.VaultSyncError, match="wasn't present"):
        vs.classify_diff(tmp_path, VaultLayout(tmp_path), "base-sha", "head-sha")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -k classify_diff -v`
Expected: FAIL with `AttributeError: module 'artmind.vault_sync' has no attribute 'classify_diff'`

- [ ] **Step 3: Implement**

Append to `artmind/vault_sync.py`:

```python
from dataclasses import dataclass, field


@dataclass
class SyncPlan:
    base: str
    head: str
    replay_docs: list[tuple[str, str]] = field(default_factory=list)        # (domain, docdir)
    retract: list[tuple[str, str]] = field(default_factory=list)            # (domain, doc_id)
    regenerate_tables: list[tuple[str, str]] = field(default_factory=list)  # (domain, table_name)


def _in_scope(domain: str, domains: list[str] | None) -> bool:
    if not domains:
        return True
    return any(domain == d or domain.startswith(d + ".") for d in domains)


def _classify_kg_diff(
    vault_dir: Path, base: str, head: str, domains: list[str] | None
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Track A (spec §4.A): replay/retract for every document (and
    table__<name>) folder under `.artmind/data/kg/**`. Driven entirely by
    git's own status letters for `observations.json` specifically -- never
    the live filesystem, which track B may already have written fresh,
    uncommitted content into by the time this runs in the same sync (spec
    §5's sequencing guarantee falls out of this for free, since `_show`
    below only ever reads committed history)."""
    import paths

    kg_dir = paths.KG_DIR
    rows = _diff_name_status(vault_dir, base, head, kg_dir)
    kg_rel = kg_dir.relative_to(vault_dir)

    replay: list[tuple[str, str]] = []
    retract: list[tuple[str, str]] = []
    for status, path in rows:
        rel = Path(path).relative_to(kg_rel)
        parts = rel.parts
        if len(parts) != 3 or parts[2] != "observations.json":
            continue
        domain, docdir, _ = parts
        if not _in_scope(domain, domains):
            continue
        if status in ("A", "M"):
            replay.append((domain, docdir))
        elif status == "D":
            base_content = _show(vault_dir, base, path)
            if base_content is None:
                raise VaultSyncError(
                    f"{path} is marked removed since {base}, but wasn't present at {base} "
                    "either -- diff and history disagree, refusing to guess"
                )
            import json
            doc_id = json.loads(base_content)["id"]
            retract.append((domain, doc_id))
    return replay, retract


def _classify_structured_text_diff(
    vault_dir: Path, base: str, head: str, domains: list[str] | None
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Track B (spec §4.B): regenerate/retract for every table's CSV under
    `.artmind/data/structured_text/**`. `manifest.json` is a single shared
    file (not "under a given table's export"); a manifest-only change with
    no accompanying CSV diff triggers nothing in this version -- a
    documented scoping decision, not an oversight."""
    import paths

    st_dir = paths.STRUCTURED_TEXT_DIR
    rows = _diff_name_status(vault_dir, base, head, st_dir)
    st_rel = st_dir.relative_to(vault_dir)

    regenerate: list[tuple[str, str]] = []
    retract: list[tuple[str, str]] = []
    for status, path in rows:
        rel = Path(path).relative_to(st_rel)
        parts = rel.parts
        if len(parts) != 2 or not parts[1].endswith(".csv"):
            continue
        domain, filename = parts
        table_name = filename[: -len(".csv")]
        if not _in_scope(domain, domains):
            continue
        if status in ("A", "M"):
            regenerate.append((domain, table_name))
        elif status == "D":
            retract.append((domain, f"table:{domain}:{table_name}"))
    return regenerate, retract


def classify_diff(
    vault_dir: Path, layout, base: str, head: str, domains: list[str] | None = None
) -> SyncPlan:
    """The full classified diff for one sync run (spec §4), over the FIXED
    `base..head` range the caller has already resolved -- never re-queried
    mid-run, which is what makes §5's sequencing guarantee hold."""
    plan = SyncPlan(base=base, head=head)
    plan.replay_docs, kg_retract = _classify_kg_diff(vault_dir, base, head, domains)
    plan.regenerate_tables, table_retract = _classify_structured_text_diff(vault_dir, base, head, domains)

    seen: set[tuple[str, str]] = set()
    for domain, doc_id in kg_retract + table_retract:
        if (domain, doc_id) not in seen:
            seen.add((domain, doc_id))
            plan.retract.append((domain, doc_id))
    return plan
```

`layout` is currently unused inside `classify_diff`/the two `_classify_*` helpers (they read `paths.KG_DIR`/`paths.STRUCTURED_TEXT_DIR` directly, which are already vault-relative once `paths` has resolved the active vault) — keep the parameter anyway, since Task 9's `sync()` has one in scope already and a future domain-scoping refinement may need it; do not remove it as "unused" without checking Task 9 first.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py -v`
Expected: PASS (all tests from Tasks 7 and 8)

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "feat(vault): classify_diff for track A/B diff classification"
```

---

### Task 9: `sync()` orchestrator (bootstrap + full sequencing)

**Files:**
- Modify: `artmind/vault_sync.py` (add `sync`)
- Test: `test/test_vault_sync.py`

- [ ] **Step 1: Write the failing tests**

Append to `test/test_vault_sync.py`:

```python
# ── sync(): bootstrap ────────────────────────────────────────────────────────


def test_sync_refuses_without_marker_or_bootstrap_flag(repo, monkeypatch):
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")

    with pytest.raises(vs.VaultSyncError, match="bootstrapEmpty"):
        vs.sync(repo)


def test_sync_bootstrap_synced_stamps_the_cursor_without_replaying(repo, monkeypatch):
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    from artmind.vault import VaultLayout, read_state

    result = vs.sync(repo, bootstrap_synced=True)

    assert result["last_synced_commit"] == vs.head_sha(repo)
    assert read_state(VaultLayout(repo))["last_synced_commit"] == vs.head_sha(repo)


def test_sync_bootstrap_synced_dry_run_writes_nothing(repo, monkeypatch):
    _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    from artmind.vault import VaultLayout, read_state

    vs.sync(repo, bootstrap_synced=True, dry_run=True)

    assert read_state(VaultLayout(repo)) == {}


def test_sync_dry_run_reports_counts_and_writes_nothing(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    from artmind.vault import VaultLayout, read_state

    result = vs.sync(repo, bootstrap_empty=True, dry_run=True)

    assert result["replay"] == 1
    assert read_state(VaultLayout(repo)) == {}


# ── sync(): the full sequencing, with Neo4j/table2graph mocked ──────────────


def _patch_ingest_and_projection(monkeypatch, deferred_keys_by_doc=None, retract_results=None):
    """Mocks the graph-touching seams `sync()` calls, so these tests stay
    hermetic (no real Neo4j) while still asserting on what was actually
    called and with what -- never on a summary count alone (CLAUDE.md)."""
    import artmind.ingest as ing
    import artmind.table2graph as t2g

    calls = {"write_to_neo4j": [], "retract_document": [], "rebuild_in_batches": [], "sweeps": []}

    def _fake_write(doc_kg_dir, domain, defer_rebuild=False):
        calls["write_to_neo4j"].append((str(doc_kg_dir), domain))
        keys = (deferred_keys_by_doc or {}).get(doc_kg_dir.name, [])
        return {"deferred_keys": keys, "unembedded_chunk_ids": []}

    def _fake_retract(doc_id, domain):
        calls["retract_document"].append((doc_id, domain))
        result = (retract_results or {}).get(doc_id, {"affected_keys": []})
        return {"doc_id": doc_id, "domain": domain, **result}

    def _fake_rebuild_in_batches(keys):
        calls["rebuild_in_batches"].append(sorted(keys))
        return {"rebuilt": len(keys), "deleted": 0, "absent": 0, "keys": len(keys), "batches": 1}

    monkeypatch.setattr(ing, "_write_to_neo4j", _fake_write)
    monkeypatch.setattr(ing, "retract_document", _fake_retract)
    monkeypatch.setattr(t2g, "_rebuild_in_batches", _fake_rebuild_in_batches)
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: calls["sweeps"].append(("entities", domain)) or 0)
    monkeypatch.setattr(ing, "_sweep_chunk_embeddings", lambda **kw: calls["sweeps"].append(("chunks", kw.get("domain"))) or 0)
    return calls


def test_sync_replays_an_added_document_folder_and_advances_the_cursor(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    calls = _patch_ingest_and_projection(
        monkeypatch, deferred_keys_by_doc={"doc1": [["Acme", "ORG", "banking"]]}
    )

    result = vs.sync(repo, bootstrap_empty=True)

    assert calls["write_to_neo4j"] == [(str(kg_dir / "banking" / "doc1"), "banking")]
    assert calls["rebuild_in_batches"] == [[("Acme", "ORG", "banking")]]
    assert ("entities", "banking") in calls["sweeps"]
    assert ("chunks", "banking") in calls["sweeps"]
    assert result["last_synced_commit"] == vs.head_sha(repo)

    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo))["last_synced_commit"] == vs.head_sha(repo)


def test_sync_retracts_a_removed_document_folder(repo, monkeypatch):
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    base = vs.head_sha(repo)
    import shutil
    shutil.rmtree(kg_dir / "banking" / "doc1")
    _commit_all(repo, "remove doc1")
    from artmind.vault import VaultLayout, write_state
    write_state(VaultLayout(repo), {"last_synced_commit": base})

    calls = _patch_ingest_and_projection(
        monkeypatch, retract_results={"docid-1": {"affected_keys": [["Acme", "ORG", "banking"]]}}
    )

    result = vs.sync(repo)

    assert calls["retract_document"] == [("docid-1", "banking")]
    assert calls["rebuild_in_batches"] == [[("Acme", "ORG", "banking")]]
    assert result["retracted"] == 1


def test_sync_leaves_the_cursor_untouched_on_failure(repo, monkeypatch):
    """§9's all-or-nothing: a transient failure mid-run must not advance
    last_synced_commit -- the next invocation recomputes the identical
    diff_range from scratch."""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")

    import artmind.ingest as ing

    def _boom(*a, **k):
        raise RuntimeError("neo4j connection dropped")

    monkeypatch.setattr(ing, "_write_to_neo4j", _boom)

    with pytest.raises(RuntimeError, match="neo4j connection dropped"):
        vs.sync(repo, bootstrap_empty=True)

    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo)) == {}


def test_sync_retry_after_a_failure_converges_to_the_same_end_state(repo, monkeypatch):
    """§9/§12's idempotent-retry property: a simulated mid-run failure,
    followed by a second run, converges to the same end state as an
    uninterrupted run would have -- the identical diff_range is recomputed
    from scratch and this time succeeds, advancing the cursor to the same
    HEAD an uninterrupted first attempt would have reached. (The
    redundant-History-version cost §9 names -- a document that had already
    committed once before the failure gets re-demoted-and-rewritten on
    retry -- is a real Neo4j-write-level effect this mocked test cannot
    observe; it is accepted per §9, not asserted away, and not re-proven
    here beyond this convergence property.)"""
    kg_dir = _patch_kg_dir(monkeypatch, repo)
    _patch_structured_text_dir(monkeypatch, repo)
    _write_doc_folder(kg_dir, "banking", "doc1", "docid-1")
    _commit_all(repo, "add doc1")
    expected_head = vs.head_sha(repo)

    import artmind.ingest as ing
    monkeypatch.setattr(ing, "_write_to_neo4j", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        vs.sync(repo, bootstrap_empty=True)

    from artmind.vault import VaultLayout, read_state
    assert read_state(VaultLayout(repo)) == {}

    _patch_ingest_and_projection(monkeypatch, deferred_keys_by_doc={"doc1": [["Acme", "ORG", "banking"]]})
    result = vs.sync(repo, bootstrap_empty=True)

    assert result["last_synced_commit"] == expected_head
    assert read_state(VaultLayout(repo))["last_synced_commit"] == expected_head


def test_sync_regenerates_a_table_from_structured_text_diff(repo, monkeypatch):
    st_dir = _patch_structured_text_dir(monkeypatch, repo)
    _patch_kg_dir(monkeypatch, repo)
    (st_dir / "banking").mkdir(parents=True)
    (st_dir / "banking" / "accounts.csv").write_text("id\n1\n")
    (st_dir / "manifest.json").write_text("{}")
    _commit_all(repo, "add table text")

    import artmind.table2graph as t2g
    from artmind.structured import registry as structured_registry
    from artmind.structured.text_export import import_structured_text as real_import

    calls = {"import_structured_text": [], "table_to_graph": []}
    monkeypatch.setattr(
        "artmind.structured.text_export.import_structured_text",
        lambda *a, **k: calls["import_structured_text"].append(k.get("tables")) or {"tables_loaded": 1},
    )
    monkeypatch.setattr(
        structured_registry, "get_table",
        lambda table_name, domain=None: {"id": 1, "domain": "banking", "table_name": "accounts"},
    )
    monkeypatch.setattr(t2g, "find_mappings", lambda table_name, domain: [object()])
    monkeypatch.setattr("artmind.temporal.load_schema", lambda domain: {"name": domain})
    monkeypatch.setattr(
        t2g, "table_to_graph",
        lambda row, mapping, **k: calls["table_to_graph"].append(k) or {
            "commit": {"deferred_keys": [("Checking", "ACCOUNT", "banking")]}
        },
    )
    monkeypatch.setattr(t2g, "stage_dir", lambda domain, table_name: repo / "staged" / domain / table_name)
    calls2 = _patch_ingest_and_projection(monkeypatch)

    result = vs.sync(repo, bootstrap_empty=True)

    assert calls["import_structured_text"] == [[("banking", "accounts")]]
    assert calls["table_to_graph"][0]["defer_rebuild"] is True
    assert calls2["rebuild_in_batches"] == [[("Checking", "ACCOUNT", "banking")]]
    assert result["regenerated_tables"] == 1
```

Adjust the monkeypatch target strings above (`"artmind.structured.text_export.import_structured_text"`) to whatever import style Task 9's actual `sync()` implementation uses internally (module-level `from ... import ...` vs. `import ... as ...`) — if `sync()` does `from artmind.structured.text_export import import_structured_text` as a local import inside the function body (as designed below), patch it via `monkeypatch.setattr("artmind.structured.text_export.import_structured_text", ...)` (patches the origin, which a fresh `from ... import` inside the function picks up each call) — confirm this resolves correctly once Step 3 is implemented; if not, patch `vs.import_structured_text` instead by making `sync()` import it at module level in `vault_sync.py` instead of inside the function.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault_sync.py -k "test_sync_" -v`
Expected: FAIL with `AttributeError: module 'artmind.vault_sync' has no attribute 'sync'`

- [ ] **Step 3: Implement**

Append to `artmind/vault_sync.py`:

```python
def sync(
    vault_dir: Path,
    *,
    domains: list[str] | None = None,
    bootstrap_empty: bool = False,
    bootstrap_synced: bool = False,
    dry_run: bool = False,
) -> dict:
    """Replay committed KG-staging and structured-text changes into
    Neo4j/DuckDB since the last sync (spec §5). All-or-nothing: the cursor
    only advances on full success (§9); any exception here propagates
    unchanged, leaving `last_synced_commit` untouched so the next
    invocation recomputes the identical diff_range from scratch.
    """
    from artmind.vault import VaultLayout, read_state, write_state

    if bootstrap_empty and bootstrap_synced:
        raise VaultSyncError("pass at most one of --bootstrapEmpty / --bootstrapSynced")

    layout = VaultLayout(vault_dir)
    state = read_state(layout)
    last_synced = state.get("last_synced_commit")
    head = head_sha(vault_dir)

    if bootstrap_synced:
        if dry_run:
            return {"bootstrap": "synced", "dry_run": True, "would_stamp": head}
        write_state(layout, {"last_synced_commit": head})
        return {"bootstrap": "synced", "last_synced_commit": head}

    if last_synced is None and not bootstrap_empty:
        raise VaultSyncError(
            "no last_synced_commit recorded yet -- pass --bootstrapEmpty for a full "
            "replay of everything (correct for an empty Neo4j/DuckDB), or "
            "--bootstrapSynced if this machine's graph is already known-current "
            "(e.g. right after `session initiate`/`db restore`)"
        )
    base = EMPTY_TREE_SHA if bootstrap_empty else last_synced

    plan = classify_diff(vault_dir, layout, base, head, domains)

    if dry_run:
        return {
            "dry_run": True,
            "base": base,
            "head": head,
            "replay": len(plan.replay_docs),
            "retract": len(plan.retract),
            "regenerate_tables": len(plan.regenerate_tables),
        }

    from artmind import ingest
    from artmind.structured import registry as structured_registry
    from artmind.structured.text_export import import_structured_text
    from artmind.table2graph import find_mappings, stage_dir, table_to_graph, _rebuild_in_batches
    from artmind.temporal import load_schema
    from paths import KG_DIR

    all_keys: set[tuple[str, str, str]] = set()
    regenerated_dirs: list[Path] = []

    # ── track B: regenerate (§5 step 2) -- BEFORE track A, so its output
    #    (freshly regenerated table__* JSON, uncommitted) can never itself
    #    be mistaken for a track-A input in this same run (the diff_range
    #    above is already fixed from `plan`, computed before any of this
    #    runs).
    if plan.regenerate_tables:
        import_structured_text(tables=plan.regenerate_tables)
        for domain, table_name in plan.regenerate_tables:
            row = structured_registry.get_table(table_name, domain=domain)
            if row is None:
                raise VaultSyncError(f"{domain}/{table_name}: not found in the registry after restore")
            found = find_mappings(table_name, domain)
            if not found:
                raise VaultSyncError(f"{domain}/{table_name}: no table mapping under domains/table_mappings/")
            if len(found) > 1:
                raise VaultSyncError(f"{domain}/{table_name}: ambiguous, {len(found)} mappings match")
            schema = load_schema(domain)
            if not schema:
                raise VaultSyncError(f"{domain}/{table_name}: no schema for domain {domain!r}")
            report = table_to_graph(row, found[0], schema=schema, embed=False, defer_rebuild=True)
            all_keys.update(tuple(k) for k in report["commit"].get("deferred_keys") or [])
            regenerated_dirs.append(stage_dir(domain, table_name))

    # ── track A: replay + retract (§5 step 3) ───────────────────────────────
    for domain, docdir in plan.replay_docs:
        doc_kg_dir = KG_DIR / domain / docdir
        summary = ingest._write_to_neo4j(doc_kg_dir, domain, defer_rebuild=True)
        if summary is None:
            raise VaultSyncError(f"{doc_kg_dir}: staged KG JSON could not be read")
        all_keys.update(tuple(k) for k in summary.get("deferred_keys") or [])

    for domain, doc_id in plan.retract:
        result = ingest.retract_document(doc_id, domain)
        all_keys.update(tuple(k) for k in result.get("affected_keys") or [])

    # ── the union rebuild (§5 step 4), chunked exactly like table2graph's own ─
    if all_keys:
        projection_summary = _rebuild_in_batches(sorted(all_keys))
    else:
        projection_summary = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": 0}

    # ── domain-scoped embed sweeps (§5 step 5) ──────────────────────────────
    touched_domains = sorted({k[2] for k in all_keys})
    for d in touched_domains:
        domain_keys = [k for k in all_keys if k[2] == d]
        ingest._sweep_embeddings(d, domain_keys)
        ingest._sweep_chunk_embeddings(domain=d)

    # ── commit track B's freshly regenerated table__* folders (§5 step 6) ───
    if regenerated_dirs:
        from artmind import vault_git
        vault_git.commit_paths(
            regenerated_dirs,
            f"vault sync: regenerate {len(regenerated_dirs)} table(s) from structured text",
        )

    # ── only on full success, advance the cursor (§5 step 7) ────────────────
    write_state(layout, {"last_synced_commit": head})

    return {
        "base": base,
        "last_synced_commit": head,
        "replayed": len(plan.replay_docs),
        "retracted": len(plan.retract),
        "regenerated_tables": len(plan.regenerate_tables),
        "projection": projection_summary,
        "domains_swept": touched_domains,
    }
```

Note the local imports (`import_structured_text`, `table_to_graph`, etc.) are deliberately function-scoped, matching this codebase's own convention elsewhere (`ingest.py`'s `_commit_document_tx` does the same for `projection`/`same_as`) — keeps `vault_sync.py` importable without pulling in the full Neo4j/DuckDB stack at module load time, and keeps the tests' monkeypatching targeting the origin modules simple.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_sync.py -v`
Expected: PASS (all tests from Tasks 7, 8, 9)

If the `test_sync_regenerates_a_table_from_structured_text_diff` monkeypatch targets don't line up with the actual local-import style (a common TDD friction point with function-scoped imports), fix the test's patch targets to match the real import path `sync()` resolves at call time — do NOT change `sync()`'s import style just to make patching easier; function-scoped imports here are a deliberate, existing codebase convention.

- [ ] **Step 5: Commit**

```bash
git add artmind/vault_sync.py test/test_vault_sync.py
git commit -m "feat(vault): implement vault_sync.sync() — full track A/B sequencing"
```

---

### Task 10: CLI — `vault` group with `status` and `sync`

**Files:**
- Modify: `artmind/cli.py` (replace the bare `vault` command around line 3341 with a group)
- Modify: `test/test_vault_cli.py` (update 2 existing bare-invocation tests)
- Test: `test/test_vault_cli.py` (new tests for `vault sync`)

- [ ] **Step 1: Update the two existing bare-invocation tests**

In `test/test_vault_cli.py`, change:

```python
    result = CliRunner().invoke(cli, ["vault"])
```

to:

```python
    result = CliRunner().invoke(cli, ["vault", "status"])
```

in both `test_vault_reports_the_active_vault` and `test_vault_outside_a_vault_explains_rather_than_guessing` (see the plan header's "Two deliberate deviations" note #2 for why bare `artmind vault` is not preserved as a runnable group).

- [ ] **Step 2: Write the new failing tests**

Append to `test/test_vault_cli.py`:

```python
def test_vault_bare_invocation_shows_help_not_status(tmp_path, monkeypatch):
    """`vault` is now a plain group -- bare invocation shows --help and exits
    0, the same convention every other group in this CLI already follows
    (db, ingest, domains, query, ...). Use `vault status` for the status
    report."""
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init"])

    result = CliRunner().invoke(cli, ["vault"])

    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def _init_and_commit(tmp_path):
    """`artmind init` only runs `git init` -- never a commit -- so every
    `vault sync` CLI test needs at least one real commit before it can call
    `head_sha()` successfully (see `vault_sync.head_sha`'s "no commits yet"
    handling in Task 7)."""
    CliRunner().invoke(cli, ["init"])
    (tmp_path / "note.md").write_text("hello\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=tmp_path, check=True)


def test_vault_sync_refuses_without_marker_or_bootstrap_flag(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)

    result = CliRunner().invoke(cli, ["vault", "sync"])

    assert result.exit_code != 0
    assert "bootstrapEmpty" in result.output


def test_vault_sync_bootstrap_synced_stamps_the_cursor(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)

    result = CliRunner().invoke(cli, ["vault", "sync", "--bootstrapSynced", "--compact"])

    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output)
    assert payload["bootstrap"] == "synced"
    assert "last_synced_commit" in payload


def test_vault_sync_dry_run_reports_without_writing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _init_and_commit(tmp_path)

    result = CliRunner().invoke(cli, ["vault", "sync", "--bootstrapEmpty", "--dryRun", "--compact"])

    assert result.exit_code == 0, result.output
    import json
    payload = json.loads(result.output)
    assert payload["dry_run"] is True
    from artmind import vault as vault_mod
    assert vault_mod.read_state(vault_mod.VaultLayout(tmp_path)) == {}
```

`subprocess` needs to be imported at the top of `test/test_vault_cli.py` if it isn't already (it is — see `test_init_runs_git_init_when_the_directory_is_not_a_repo`'s existing use of it, line 4's `import subprocess`).

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --group dev pytest test/test_vault_cli.py -v`
Expected: FAIL — the two updated tests fail with the old bare `["vault"]` semantics gone, and the new `vault sync` tests fail with `Error: No such command 'sync'`

- [ ] **Step 4: Implement**

In `artmind/cli.py`, replace the existing `vault_status` command (around line 3338-3389) with:

```python
# ── artmind vault ─────────────────────────────────────────────────────────────


def _vault_status_impl(compact: bool) -> None:
    """Show which vault is active and how it was resolved.

    Human-readable by default rather than JSON-first like `projection status`:
    this exists to be glanced at, and a wrong answer here means every other
    command is operating on the wrong knowledge base.
    """
    from artmind import vault as vault_mod
    from paths import ARTMIND_DATA_DIR, ARTMIND_HOME, LOADED_ENV_FILES

    # Resolved fresh here rather than read off `paths.ARTMIND_VAULT_DIR`:
    # that constant is computed once, at process import, from the cwd at that
    # moment. This command's entire job is to say which vault the CURRENT
    # directory resolves to, so it must re-walk now, not report a stale answer
    # (it is never proxied to the `serve` daemon, so "now" always means this
    # invocation's own cwd).
    try:
        vault_dir = vault_mod.resolve_vault()
    except vault_mod.VaultError as e:
        raise click.ClickException(str(e))

    if vault_dir is None:
        raise click.ClickException(
            "Not inside an artmind vault.\n"
            "  cd into one, or run `artmind init` to make this directory a vault."
        )

    layout = vault_mod.VaultLayout(vault_dir)
    info = {
        "vault": str(vault_dir),
        "artmind_dir": str(ARTMIND_HOME),
        "data_dir": str(ARTMIND_DATA_DIR),
        "manifest": str(layout.vault_yaml) if layout.vault_yaml.is_file() else None,
        "config": [str(p) for p in LOADED_ENV_FILES],
        "graph": {
            "uri": os.environ.get("ARTMIND_KG_NEO4J_URI", ""),
            "database": os.environ.get("ARTMIND_KG_NEO4J_DATABASE", ""),
        },
    }
    if compact:
        _echo_json(info, compact=True)
        return
    click.echo(f"Vault:    {info['vault']}")
    click.echo(f"Data:     {info['data_dir']}")
    click.echo(f"Manifest: {info['manifest'] or '(none — run artmind init)'}")
    click.echo(f"Config:   {', '.join(info['config']) or '(none loaded)'}")
    click.echo(f"Graph:    {info['graph']['uri'] or '(unset)'}  db={info['graph']['database'] or '(unset)'}")


@cli.group("vault")
def vault():
    """Which vault is active (`status`), and git-diff-driven sync into Neo4j/the structured store (`sync`)."""
    pass


@vault.command("status")
@click.option("--compact", is_flag=True, help="Emit compact JSON instead of the summary")
def vault_status(compact: bool):
    """Show which vault is active and how it was resolved."""
    _vault_status_impl(compact)


@vault.command("sync")
@click.option(
    "--bootstrapEmpty", "bootstrap_empty", is_flag=True,
    help="First sync ever: replay everything committed, from the vault's very first commit. "
    "Slow for a large vault, but correct for a genuinely empty Neo4j/DuckDB.",
)
@click.option(
    "--bootstrapSynced", "bootstrap_synced", is_flag=True,
    help="Stamp the cursor at HEAD with no replay at all -- for right after a full "
    "`session initiate`/`db restore`, where the graph is already known-current.",
)
@click.option("--domain", "domain", multiple=True, help="Domain(s) to scope the sync (repeatable; comma-splittable). Default: every domain.")
@click.option("--dryRun", "dry_run", is_flag=True, help="Report the classified diff (documents to replay/retract, tables to regenerate) without writing anything.")
@click.option("--compact", is_flag=True, help="Emit compact JSON")
def vault_sync_cmd(bootstrap_empty, bootstrap_synced, domain, dry_run, compact):
    """Replay committed KG-staging and structured-text changes into Neo4j/DuckDB since the last sync.

    Detects exactly which document folders under .artmind/data/kg/** and which
    structured-store tables under .artmind/data/structured_text/** changed in
    git since the last `vault sync`, and replays only those — incremental,
    CDC-like, and git-native. Complements (does not replace) `session close`/
    `session initiate`'s whole-graph snapshot. Run with no marker yet? pass
    --bootstrapEmpty or --bootstrapSynced (see each flag's own help).
    """
    _setup_logger()
    from artmind import vault as vault_mod
    from artmind.vault_sync import VaultSyncError, sync as vault_sync_fn

    try:
        vault_dir = vault_mod.resolve_vault()
    except vault_mod.VaultError as e:
        raise click.ClickException(str(e))
    if vault_dir is None:
        raise click.ClickException(
            "Not inside an artmind vault.\n"
            "  cd into one, or run `artmind init` to make this directory a vault."
        )
    try:
        result = vault_sync_fn(
            vault_dir,
            domains=_parse_domains(domain) if domain else None,
            bootstrap_empty=bootstrap_empty,
            bootstrap_synced=bootstrap_synced,
            dry_run=dry_run,
        )
    except VaultSyncError as e:
        raise click.ClickException(str(e))
    _echo_json(result, compact)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --group dev pytest test/test_vault_cli.py -v`
Expected: PASS (all)

- [ ] **Step 6: Run the CLI guide coverage test**

Run: `uv run --group dev pytest test/test_cli_guide.py -v`
Expected: PASS — `vault status`/`vault sync` are reached automatically via `get_panels`'s existing fallback (no `COMMAND_GROUPS` entry needed; confirmed during planning by tracing `build_sections()`/`get_panels()` against how the existing `domains` group is routed)

- [ ] **Step 7: Commit**

```bash
git add artmind/cli.py test/test_vault_cli.py
git commit -m "feat(cli): promote vault to a group with status and sync subcommands"
```

---

### Task 11: Full regression pass

**Files:** none (verification only)

- [ ] **Step 1: Run the full hermetic suite**

Run: `uv run --group dev pytest test/ -v`
Expected: PASS — all pre-existing tests plus every test added in Tasks 1-10 (roughly 940+ tests, ~10-15s, no Neo4j/network per `test/conftest.py`'s autouse guard)

- [ ] **Step 2: Dump the real CLI tree and eyeball `vault`'s placement**

Run: `just dev-cli-help`
Expected: `artmind vault status` and `artmind vault sync` both appear under "Setup & tools", with `sync`'s `--bootstrapEmpty`/`--bootstrapSynced`/`--domain`/`--dryRun`/`--compact` options listed

- [ ] **Step 3: Push the change to the run folder and do one real, manual, non-hermetic smoke check**

This step needs a real vault with a real Neo4j reachable (the user has one available; ask for the bolt port/password per their own note if not already in `~/.artmind` / the vault's own `.artmind/config.env`, since `~/.artmind/config.env` does not carry them for this environment).

```bash
just dev-install
```

Then, from inside an actual vault (or a scratch one created with `artmind init` for this check):

```bash
ARTMIND_NO_PROXY=1 artmind vault status
ARTMIND_NO_PROXY=1 artmind vault sync --dryRun --compact
```

Expected: `vault status` reports the vault/graph config as before; `vault sync --dryRun` either reports a classified diff (if the vault already has committed KG/structured-text data) or refuses cleanly with the bootstrap-flag message (if `.artmind/state.json` has no `last_synced_commit` yet) — confirm the refusal message actually names `--bootstrapEmpty`/`--bootstrapSynced`.

- [ ] **Step 4: No commit for this task** — it is verification-only. If Step 1 or 2 surfaces a regression, fix it as a new commit referencing which task's change caused it, then re-run Steps 1-2.

---

## Summary of what this plan does NOT do (explicitly out of scope, matching the spec's own §2 non-goals plus the two deviations noted at the top)

- No automatic trigger (git hook, every-invocation check) — `vault sync` is only ever run explicitly.
- No `same_as.yaml`/schema-diff-scoped partial rebuild — that remains the existing full-rebuild drift-detection path.
- No `[ingest]`-extra gate for track B (deviation #1 above).
- No preservation of bare `artmind vault`'s old single-command behavior (deviation #2 above) — `artmind vault status` replaces it.
- No changes to `graph_snapshot.py` (the spec's §13 "record `vault_git.current_commit()` in `export_graph`'s meta" idea is explicitly left as a future nice-to-have, not required here).
- No new skill file or `justfile` recipe for `vault sync` — CLAUDE.md's "update the skill + justfile together" convention is about documentation staying in sync with an *existing* documented command; there is no pre-existing `artmind-ingestion-helper` mention of `vault` to keep current, and adding a brand-new skill section is a documentation task the user can decide on separately.
