"""Git-commitable text export of the structured store (docs/vault.md, "What is
in git, and what is not"). `.artmind/data/structured/`'s parquet + DuckDB
catalog are a rebuildable cache; this module writes the diffable source of
truth they're rebuilt from -- one CSV per registered table, plus a
`manifest.json` carrying the registry rows (`registry.dump_all()`'s shape)
needed to rebuild them: `refresh_mode`, `business_key`, confirmed grain/
column-mapping/bridge-role classifications, and each column's dtype. Without
the manifest, gitignoring the parquet/DuckDB catalog would be a straight
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

MANIFEST_NAME = "manifest.json"


def _quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _table_csv_path(dest_dir: Path, domain: str, table_name: str) -> Path:
    return dest_dir / domain / f"{table_name}.csv"


def export_structured_text(dest_dir: Path | None = None, *, tables: list[dict] | None = None) -> dict:
    """Write CSV + `manifest.json` for `tables` (default: every registered
    table) to `dest_dir` (default: `paths.STRUCTURED_TEXT_DIR`).

    Row order is `ORDER BY ALL` (every column, left to right) -- deterministic
    across re-exports of unchanged data, so a re-export that changed nothing
    produces a byte-identical CSV and `vault_git.commit_paths` correctly
    no-ops instead of committing a spurious reordering.

    The manifest is always the FULL registry dump, even when `tables` scopes
    which CSVs get rewritten -- it's one shared file, cheap to rewrite in
    full, and a partial manifest would leave every other table's `db reindex`
    unusable. Returns `{"tables": <count exported>, "files": [<paths written>]}`,
    for the caller to hand straight to `vault_git.commit_paths`.
    """
    dest_dir = Path(dest_dir) if dest_dir else paths.STRUCTURED_TEXT_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)

    target_tables = tables if tables is not None else registry.list_tables()

    ds = DuckDBDatasource()
    ds.ensure_views(registry.list_tables())

    written: list[Path] = []
    for table in target_tables:
        csv_path = _table_csv_path(dest_dir, table["domain"], table["table_name"])
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        view = view_name(table["domain"], table["table_name"])
        ds.con.execute(
            f"COPY (SELECT * FROM {_quote_ident(view)} ORDER BY ALL) TO "
            f"{_quote_literal(str(csv_path))} (FORMAT CSV, HEADER, NULL {_quote_literal(NULL_SENTINEL)})"
        )
        written.append(csv_path)

    manifest_path = dest_dir / MANIFEST_NAME
    manifest = registry.dump_all()
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str, sort_keys=True),
        encoding="utf-8",
    )
    written.append(manifest_path)

    logger.info("structured text export: {} table(s) -> {}", len(target_tables), dest_dir)
    return {"tables": len(target_tables), "files": [str(p) for p in written]}


def _csv_header(csv_path: Path) -> list[str]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        row = next(csv.reader(f), None)
    if row is None:
        raise ValueError(f"{csv_path}: empty file, expected at least a header row")
    return row


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
