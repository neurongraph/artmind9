"""Structured-file ingestion: csv/xlsx -> parquet + registry rows (replace mode).

Mirrors ``artmind/ingest.py``'s sha256 dedup shape, but the unit of dedup is
per registered table rather than per document.
"""

import dataclasses
import json
from pathlib import Path

import click

from artmind.ingest import _compute_sha256
from artmind.structured import registry, sanitize_identifier
from artmind.structured.duckdb_adapter import DuckDBDatasource, parquet_path_for, structured_db_path

DATASOURCE_NAME = "default"


def _sheet_is_empty(ws) -> bool:
    for row in ws.iter_rows(values_only=True):
        if any(v is not None for v in row):
            return False
    return True


def _enumerate_source_tables(
    source: Path, *, table: str | None, sheet: str | None, header_row: int
) -> list[dict]:
    """Return ``[{"table_name": ..., "sheet": sheet_or_None}]`` for the file."""
    if source.suffix.lower() == ".csv":
        return [{"table_name": sanitize_identifier(table or source.stem), "sheet": None}]

    from openpyxl import load_workbook

    wb = load_workbook(source, read_only=True, data_only=True)
    try:
        sheet_names = [
            name
            for name in wb.sheetnames
            if wb[name].sheet_state == "visible" and not _sheet_is_empty(wb[name])
        ]
    finally:
        wb.close()

    if sheet:
        if sheet not in sheet_names:
            raise click.ClickException(
                f"sheet '{sheet}' not found (or empty/hidden) in '{source.name}'"
            )
        sheet_names = [sheet]

    if not sheet_names:
        raise click.ClickException(f"no non-empty, visible sheets found in '{source.name}'")

    if len(sheet_names) == 1:
        return [{"table_name": sanitize_identifier(table or source.stem), "sheet": sheet_names[0]}]

    stem = sanitize_identifier(source.stem)
    return [
        {"table_name": f"{stem}__{sanitize_identifier(s)}", "sheet": s} for s in sheet_names
    ]


def _header_row_values(source: Path, sheet: str | None, header_row: int) -> list | None:
    if source.suffix.lower() == ".csv":
        import csv

        with open(source, newline="") as f:
            reader = csv.reader(f)
            for _ in range(header_row):
                next(reader, None)
            return next(reader, None)

    from openpyxl import load_workbook

    wb = load_workbook(source, read_only=True, data_only=True)
    try:
        ws = wb[sheet] if sheet else wb.active
        rows = ws.iter_rows(values_only=True)
        for _ in range(header_row):
            next(rows, None)
        row = next(rows, None)
        return list(row) if row is not None else None
    finally:
        wb.close()


def _validate_header(source: Path, sheet: str | None, header_row: int) -> None:
    """Genuinely messy sheets (no clean header row) error out with guidance —
    v1 assumes reasonably tabular sheets (spec §6.1)."""
    header = _header_row_values(source, sheet, header_row)
    where = f"'{source.name}'" + (f" sheet '{sheet}'" if sheet else "")
    if not header or any(h is None or str(h).strip() == "" for h in header):
        raise click.ClickException(
            f"{where} has no clean header row (empty/missing column names)."
            " Clean the file first — see the `xlsx` skill."
        )


def _write_table(
    ds: DuckDBDatasource, source: Path, domain: str, spec: dict, file_sha256: str, header_row: int
) -> dict:
    _validate_header(source, spec["sheet"], header_row)
    row_count = ds.load_table(
        source, spec["table_name"], domain, sheet=spec["sheet"], header_row=header_row
    )
    parquet_path = parquet_path_for(domain, spec["table_name"])
    table_id = registry.register_table(
        DATASOURCE_NAME,
        spec["table_name"],
        domain,
        source_file=str(source),
        sheet=spec["sheet"],
        parquet_path=str(parquet_path),
        row_count=row_count,
        sha256=file_sha256,
    )
    profiles = ds.profile_columns(spec["table_name"])
    columns = [
        {
            "name": c.name,
            "dtype": c.dtype,
            "profile_json": json.dumps(dataclasses.asdict(profiles[c.name]))
            if c.name in profiles
            else None,
        }
        for c in ds.introspect_schema(spec["table_name"])
    ]
    registry.replace_columns(table_id, columns)
    table_row = registry.get_table(spec["table_name"], domain=domain)
    return {
        "table_name": spec["table_name"],
        "domain": domain,
        "row_count": row_count,
        "parquet_path": str(parquet_path),
        "version": table_row["version"],
    }


def ingest_structured_file(
    source: Path,
    domain: str,
    *,
    table: str | None = None,
    sheet: str | None = None,
    header_row: int = 0,
    force: bool = False,
) -> dict:
    source = Path(source)
    file_sha256 = _compute_sha256(source)

    registry.register_datasource(DATASOURCE_NAME, "duckdb", str(structured_db_path()))

    table_specs = _enumerate_source_tables(source, table=table, sheet=sheet, header_row=header_row)

    if not force:
        existing_rows = [
            registry.get_table(spec["table_name"], domain=domain) for spec in table_specs
        ]
        if existing_rows and all(
            row and row.get("sha256") == file_sha256 for row in existing_rows
        ):
            return {
                "status": "skipped",
                "domain": domain,
                "tables": [row["table_name"] for row in existing_rows],
            }

    ds = DuckDBDatasource()
    results = [
        _write_table(ds, source, domain, spec, file_sha256, header_row) for spec in table_specs
    ]
    return {"status": "ok", "tables": results}


def refresh_table(table_name: str, domain: str) -> dict:
    """Re-run the load for an already-registered ``replace``-mode table from its
    recorded ``source_file``. Bumps ``version``. Temporal mode is wired in Phase 5."""
    existing = registry.get_table(table_name, domain=domain)
    if not existing:
        raise ValueError(f"table '{table_name}' is not registered for domain '{domain}'")
    if not existing.get("source_file"):
        raise ValueError(f"table '{table_name}' has no recorded source_file to refresh from")
    if existing["refresh_mode"] != "replace":
        raise ValueError(
            f"table '{table_name}' has refresh_mode='{existing['refresh_mode']}';"
            " temporal refresh is not implemented until Phase 5"
        )

    source = Path(existing["source_file"])
    file_sha256 = _compute_sha256(source)
    ds = DuckDBDatasource()
    spec = {"table_name": table_name, "sheet": existing["sheet"]}
    result = _write_table(ds, source, domain, spec, file_sha256, header_row=0)
    return {"status": "ok", **result}
