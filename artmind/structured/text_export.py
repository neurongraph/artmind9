"""Git-commitable text export of the structured store (docs/vault.md, "What is
in git, and what is not"). `.artmind/data/structured/`'s parquet + DuckDB
catalog are a rebuildable cache; this module writes the diffable source of
truth they're rebuilt from -- one CSV per registered table, plus one
`<table>.meta.json` beside it carrying that table's registry rows (spec
2026-09-26 R5; a single shared `manifest.json` was a guaranteed merge
conflict) needed to rebuild it: `refresh_mode`, `business_key`, confirmed
grain/column-mapping/bridge-role classifications, and each column's dtype.
Without the manifest, gitignoring the parquet/DuckDB catalog would be a straight
regression -- `document_registry.db` (which holds all of this) is *already*
gitignored, and `reindex.py`'s own docstring documents the consequence today:
structured (csv/xlsx-sourced) tables are an "accepted limitation", unrebuildable
from anything. `import_structured_text` is what closes that gap.

Mirrors `structured_snapshot.py`'s export/import shape; the payload here is
plain text instead of a tar.gz of parquet bytes, and (unlike
`structured_snapshot.import_structured`) every restored table's `parquet_path`
is rewritten to the CURRENT machine's `parquet_path_for(...)` rather than the
path recorded at export time -- the whole point of this module is surviving a
clone onto a different machine, where the original absolute path is
meaningless.

CSV, not JSON: the row content is already flat/tabular, so CSV gives the
smallest, most reviewable git diff per changed row. Round-tripping through CSV
drops parquet's native types, so every table is read back with an EXPLICIT
schema taken from the manifest -- never DuckDB's own sniffer, which could
silently infer a different type than the one originally profiled. NULL vs
`""` (empty string) is disambiguated by `NULL_SENTINEL`, the same convention
Postgres/MySQL's own COPY uses -- DuckDB's CSV writer already keeps a real
empty string distinct from that sentinel (unquoted-but-present vs the bare
token), so no extra encoding is needed on top.
"""
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

from loguru import logger

import paths
from artmind.structured import registry, view_name
from artmind.structured.duckdb_adapter import DuckDBDatasource, parquet_path_for
from artmind.structured.scd2 import SYSTEM_COLUMNS

#: A temporal table's three SCD-2 bookkeeping columns are never profiled or
#: registered in `columns` (`pipeline._register_columns_and_mappings`'s
#: `exclude_system_cols` -- they're internal, not real data), but they ARE
#: real columns in the parquet/CSV and their types are fixed by
#: `scd2._seed_temporal_history`'s own literal SQL. Supplied here rather than
#: looked up, so a temporal table's CSV round-trips without needing the
#: registry to know about them.
_SYSTEM_COLUMN_DTYPES = dict.fromkeys(SYSTEM_COLUMNS[:2], "DATE") | {SYSTEM_COLUMNS[2]: "BOOLEAN"}

#: Postgres/MySQL's own COPY convention -- an unquoted sentinel a real string
#: value is exceedingly unlikely to collide with, and immediately
#: recognizable as such by a human reading the CSV.
NULL_SENTINEL = "\\N"

#: Legacy (pre-R5) form: the whole registry in one shared file. Read as a
#: fallback; the next export migrates it into per-table meta and deletes it.
MANIFEST_NAME = "manifest.json"
META_SUFFIX = ".meta.json"

#: Machine-local registry fields, never written to a committed meta file (they
#: differ per machine and would conflict on every merge). The import
#: recomputes `parquet_path` for the current machine. `id` is machine-local
#: too (spec 2026-09-26 §14 A3): SQLite's autoincrement, dropped from the
#: written meta entirely rather than merely re-derived on import, so a
#: table's meta doesn't churn every time its local id changes (e.g. after an
#: import remapped it) and two machines' meta for the same table never
#: conflicts on an id that was never shared truth.
_MACHINE_LOCAL_TABLE_FIELDS = frozenset({"parquet_path", "id"})

_DUMP_KEYS = ("columns", "column_mappings", "column_roles")


def _quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_csv_path(dest_dir: Path, domain: str, table_name: str) -> Path:
    return dest_dir / domain / f"{table_name}.csv"


def _table_meta_path(dest_dir: Path, domain: str, table_name: str) -> Path:
    return dest_dir / domain / f"{table_name}{META_SUFFIX}"


def _table_meta(dump: dict, row: dict) -> dict:
    """One table's slice of a `registry.dump_all()`-shaped dump. Carries no
    machine-local id: neither `table.id` nor any child row's `table_id` (spec
    2026-09-26 §14 A3) -- within a per-table file every child unambiguously
    belongs to that table, so the id is redundant on disk."""
    table_id = row["id"]
    return {
        "table": {k: v for k, v in row.items() if k not in _MACHINE_LOCAL_TABLE_FIELDS},
        "datasource": next(
            (d for d in dump.get("datasources", []) if d["name"] == row["datasource"]), None
        ),
        **{
            key: [
                {k: v for k, v in r.items() if k != "table_id"}
                for r in dump.get(key, [])
                if r["table_id"] == table_id
            ]
            for key in _DUMP_KEYS
        },
    }


def _write_meta(dest_dir: Path, meta: dict) -> Path:
    table = meta["table"]
    path = _table_meta_path(dest_dir, table["domain"], table["table_name"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False, default=str, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def _migrate_legacy_manifest(dest_dir: Path) -> list[Path]:
    """Split a legacy `manifest.json` into per-table meta for every table
    that has none yet, then delete it. Pure file transform: a scoped export
    must not lose the metadata of tables it didn't touch."""
    legacy = dest_dir / MANIFEST_NAME
    if not legacy.is_file():
        return []
    dump = json.loads(legacy.read_text(encoding="utf-8"))
    written = [
        _write_meta(dest_dir, _table_meta(dump, row))
        for row in dump.get("tables", [])
        if not _table_meta_path(dest_dir, row["domain"], row["table_name"]).is_file()
    ]
    legacy.unlink()
    return written


def load_structured_dump(src_dir: Path) -> dict:
    """A `registry.dump_all()`-shaped dict assembled from `src_dir`'s
    per-table `<domain>/<table>.meta.json` files. A legacy `manifest.json`
    fills in any table that has no meta file (R5 read-only compatibility).
    Every table row gets a `parquet_path` key (empty when the meta omitted
    it); `import_structured_text` overwrites it for this machine.

    Table ids are machine-local (spec 2026-09-26 §14 A3) and a current
    `.meta.json` carries none at all: this assigns each table a synthetic
    dump id -- sequential in sorted file order -- and attaches its child rows
    to it. An OLD meta file (or the legacy manifest) that still carries ids
    is read too (backwards compatible): the first table (in sorted path
    order) to record a given id keeps it, a later duplicate gets the next id
    above every recorded one. Either way, every table in the returned dump
    ends up with a unique id and each file's child rows follow their own
    table's id -- the remap-by-`(domain, table_name)` logic downstream is the
    same regardless of where the id came from."""
    legacy_path = src_dir / MANIFEST_NAME
    meta_paths = sorted(src_dir.glob(f"*/*{META_SUFFIX}"))
    if not meta_paths and not legacy_path.is_file():
        raise FileNotFoundError(
            f"No structured text export found at {src_dir} "
            f"(no <domain>/<table>{META_SUFFIX} and no legacy {MANIFEST_NAME})"
        )

    dump: dict = {"datasources": [], "tables": [], **{key: [] for key in _DUMP_KEYS}}

    def _add_datasource(ds: dict | None) -> None:
        if ds and all(d["name"] != ds["name"] for d in dump["datasources"]):
            dump["datasources"].append(ds)

    # (table row, {child key: that table's child rows}), in a stable order.
    sources: list[tuple[dict, dict[str, list[dict]]]] = []
    have: set[tuple[str, str]] = set()
    for path in meta_paths:
        meta = json.loads(path.read_text(encoding="utf-8"))
        table = dict(meta["table"])
        have.add((table["domain"], table["table_name"]))
        sources.append((table, {key: meta.get(key, []) for key in _DUMP_KEYS}))
        _add_datasource(meta.get("datasource"))

    if legacy_path.is_file():
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        for row in legacy.get("tables", []):
            if (row["domain"], row["table_name"]) not in have:
                children = {key: [r for r in legacy.get(key, []) if r["table_id"] == row["id"]] for key in _DUMP_KEYS}
                sources.append((dict(row), children))
        for ds in legacy.get("datasources", []):
            _add_datasource(ds)

    next_id = max((int(table["id"]) for table, _ in sources if "id" in table), default=0) + 1
    used: set[int] = set()
    for table, children in sources:
        if "id" in table:
            table_id = int(table["id"])
            if table_id in used:
                logger.warning(
                    "structured text: {}/{} records table id {}, already used by another table's "
                    "metadata; importing it as {} (table ids are local, spec 2026-09-26 §14 A3)",
                    table["domain"], table["table_name"], table_id, next_id,
                )
                table_id, next_id = next_id, next_id + 1
        else:
            table_id, next_id = next_id, next_id + 1
        used.add(table_id)
        table["id"] = table_id
        dump["tables"].append(table)
        for key in _DUMP_KEYS:
            dump[key].extend({**r, "table_id": table_id} for r in children[key])

    for row in dump["tables"]:
        row.setdefault("parquet_path", "")
    return dump


def export_structured_text(
    dest_dir: Path | None = None, *, tables: list[dict] | None = None, meta_only: bool = False
) -> dict:
    """Write CSV + per-table `.meta.json` for `tables` (default: every registered
    table) to `dest_dir` (default: `paths.STRUCTURED_TEXT_DIR`).

    `meta_only=True` rewrites only the `.meta.json` of a table whose CSV is
    already there -- what a registry-only curation change (`db grain`, `db
    mappings`, `db bridge`, `db propose`) needs, without re-dumping every
    row. A table with no CSV yet still gets one, so it travels at all.

    Row order is `ORDER BY ALL` (every column, left to right) -- deterministic
    across re-exports of unchanged data, so a re-export that changed nothing
    produces a byte-identical CSV and git sees no change, instead of a
    spurious reordering for Obsidian Git to commit.

    Only the touched tables' CSV and meta are written; every other table's
    files are left as they are. A legacy `manifest.json` is first split into
    per-table meta (for tables without one) and deleted. Returns
    `{"tables": <count>, "files": [<paths written>], "legacy_manifest_removed": bool}`.
    """
    dest_dir = Path(dest_dir) if dest_dir else paths.STRUCTURED_TEXT_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    target_tables = tables if tables is not None else registry.list_tables()

    csv_tables = [
        t for t in target_tables
        if not meta_only or not _table_csv_path(dest_dir, t["domain"], t["table_name"]).is_file()
    ]

    if csv_tables:
        ds = DuckDBDatasource()
        ds.ensure_views(registry.list_tables())

    written: list[Path] = []
    for table in csv_tables:
        csv_path = _table_csv_path(dest_dir, table["domain"], table["table_name"])
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        view = view_name(table["domain"], table["table_name"])
        ds.con.execute(
            f"COPY (SELECT * FROM {_quote_ident(view)} ORDER BY ALL) TO "
            f"{_quote_literal(str(csv_path))} (FORMAT CSV, HEADER, NULL {_quote_literal(NULL_SENTINEL)})"
        )
        written.append(csv_path)

    had_legacy = (dest_dir / MANIFEST_NAME).is_file()
    written.extend(_migrate_legacy_manifest(dest_dir))

    dump = registry.dump_all()
    rows_by_key = {(r["domain"], r["table_name"]): r for r in dump["tables"]}
    for table in target_tables:
        row = rows_by_key.get((table["domain"], table["table_name"]))
        if row is not None:
            written.append(_write_meta(dest_dir, _table_meta(dump, row)))

    logger.info("structured text export: {} table(s) -> {}", len(target_tables), dest_dir)
    return {
        "tables": len(target_tables),
        "files": [str(p) for p in written],
        "legacy_manifest_removed": had_legacy,
    }


def _csv_header(csv_path: Path) -> list[str]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        row = next(csv.reader(f), None)
    if row is None:
        raise ValueError(f"{csv_path}: empty file, expected at least a header row")
    return row


def import_structured_text(
    src_dir: Path | None = None, *, tables: list[tuple[str, str]] | None = None, registry_only: bool = False
) -> dict:
    """Wipe and rebuild the structured store from `src_dir`'s CSV + per-table
    `.meta.json` (legacy `manifest.json` as a fallback).

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

    `registry_only=True` (with `tables`) restores only the registry rows --
    a table's classifications changed but not its rows (`vault sync`'s
    curation-only case) -- and leaves an existing parquet file as it is; a
    table with no parquet yet is still loaded from its CSV.
    """
    if registry_only and tables is None:
        raise ValueError("registry_only needs `tables`: an unscoped import wipes every table's parquet")
    src_dir = Path(src_dir) if src_dir else paths.STRUCTURED_TEXT_DIR
    manifest = load_structured_dump(src_dir)

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
        if registry_only and Path(table["parquet_path"]).is_file():
            continue
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
                f"{csv_path}: column(s) {missing} have no recorded dtype in the table's metadata "
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
