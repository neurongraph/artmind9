"""Write mart tables the way structured ingestion does: one parquet file per table under
STRUCTURED_DIR/<domain>/, registered in the registry, exposed as <domain>__mart_<table>."""
from __future__ import annotations

import os
from pathlib import Path

import duckdb

import paths
from artmind.mart import MART_SOURCE_PREFIX, TABLE_PREFIX


def _dtype(values) -> str:
    sample = next((v for v in values if v is not None), None)
    if isinstance(sample, list):
        return "VARCHAR[]"
    if isinstance(sample, bool):
        return "BOOLEAN"
    if isinstance(sample, int):
        return "BIGINT"
    if isinstance(sample, float):
        return "DOUBLE"
    return "VARCHAR"


def _write_parquet(rows: list[dict], path: Path) -> list[dict]:
    con = duckdb.connect()
    cols = list(rows[0].keys()) if rows else ["empty"]
    types = {c: _dtype([r.get(c) for r in rows]) for c in cols}
    con.execute("CREATE TABLE t (" + ", ".join(f'"{c}" {types[c]}' for c in cols) + ")")
    if rows:
        con.executemany("INSERT INTO t VALUES (" + ", ".join("?" for _ in cols) + ")",
                        [[r.get(c) for c in cols] for r in rows])
    con.execute(f"COPY t TO '{path}' (FORMAT PARQUET)")
    con.close()
    return [{"name": c, "dtype": types[c], "profile_json": None} for c in cols]


def _register(domain, name, path, rows, columns) -> None:
    from artmind.structured import registry
    from artmind.structured.duckdb_adapter import structured_db_path
    from artmind.structured.pipeline import DATASOURCE_NAME

    registry.register_datasource(DATASOURCE_NAME, "duckdb", str(structured_db_path()))
    table_id = registry.register_table(DATASOURCE_NAME, name, domain, source_file=f"{MART_SOURCE_PREFIX}{domain}",
                                       parquet_path=str(path), row_count=rows,
                                       grain="lookup" if name.startswith(f"{TABLE_PREFIX}dim_") else "instance")
    registry.replace_columns(table_id, columns)


def write_tables(domain: str, tables: dict[str, list[dict]]) -> dict[str, int]:
    directory = Path(paths.STRUCTURED_DIR) / domain
    directory.mkdir(parents=True, exist_ok=True)
    staged = []
    for name, rows in tables.items():
        final = directory / f"{TABLE_PREFIX}{name}.parquet"
        tmp = directory / f"{TABLE_PREFIX}{name}.parquet.mart-tmp"
        columns = _write_parquet(rows, tmp)
        staged.append((name, tmp, final, len(rows), columns))
    for _, tmp, final, _, _ in staged:       # every table written: now swap them all in
        os.replace(tmp, final)
    for name, _, final, rows, columns in staged:
        _register(domain, f"{TABLE_PREFIX}{name}", final, rows, columns)
    return {name: rows for name, _, _, rows, _ in staged}
