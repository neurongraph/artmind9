"""SQLite CRUD for the structured-store registry tables.

Mirrors ``artmind/jobs.py``'s connection discipline: open via ``db._get_db()``,
commit, close in a ``finally``. ``"column"`` (a ``column_mappings`` column name)
and the ``"tables"``/``"columns"`` table names are double-quoted per
CLAUDE.md's identifier-quoting convention.
"""

import sqlite3
from datetime import datetime

from artmind.db import _get_db


def _connect() -> sqlite3.Connection:
    conn = _get_db()
    conn.row_factory = sqlite3.Row
    return conn


def register_datasource(name: str, type_: str, path_or_dsn: str) -> None:
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT OR IGNORE INTO datasources (name, type, path_or_dsn, created_at)"
            " VALUES (?, ?, ?, ?)",
            (name, type_, path_or_dsn, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def register_table(
    datasource: str,
    table_name: str,
    domain: str,
    *,
    source_file: str | None = None,
    sheet: str | None = None,
    parquet_path: str,
    row_count: int | None = None,
    sha256: str | None = None,
    refresh_mode: str = "replace",
    business_key: str | None = None,
    effective_date_column: str | None = None,
) -> int:
    """Insert or, on a ``(datasource, table_name)`` conflict, bump ``version`` and
    refresh the mutable fields. Returns the table id."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        now = datetime.now().isoformat()
        existing = cursor.execute(
            'SELECT id, version FROM "tables" WHERE datasource = ? AND table_name = ?',
            (datasource, table_name),
        ).fetchone()
        if existing:
            table_id, version = existing
            cursor.execute(
                'UPDATE "tables" SET domain = ?, source_file = ?, sheet = ?, parquet_path = ?,'
                " version = ?, row_count = ?, refresh_mode = ?, business_key = ?,"
                " effective_date_column = ?, ingested_at = ?, sha256 = ? WHERE id = ?",
                (
                    domain,
                    source_file,
                    sheet,
                    str(parquet_path),
                    version + 1,
                    row_count,
                    refresh_mode,
                    business_key,
                    effective_date_column,
                    now,
                    sha256,
                    table_id,
                ),
            )
        else:
            cursor.execute(
                'INSERT INTO "tables" (datasource, table_name, domain, source_file, sheet,'
                " parquet_path, version, row_count, refresh_mode, business_key,"
                " effective_date_column, ingested_at, sha256)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)",
                (
                    datasource,
                    table_name,
                    domain,
                    source_file,
                    sheet,
                    str(parquet_path),
                    row_count,
                    refresh_mode,
                    business_key,
                    effective_date_column,
                    now,
                    sha256,
                ),
            )
            table_id = cursor.lastrowid
        conn.commit()
        return table_id
    finally:
        conn.close()


def get_table(table_name: str, domain: str | None = None) -> dict | None:
    conn = _connect()
    try:
        if domain is not None:
            row = conn.execute(
                'SELECT * FROM "tables" WHERE table_name = ? AND domain = ?',
                (table_name, domain),
            ).fetchone()
        else:
            row = conn.execute(
                'SELECT * FROM "tables" WHERE table_name = ?', (table_name,)
            ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_tables(domains: list[str] | None = None) -> list[dict]:
    """List registered tables, optionally scoped to ``domains`` (exact match or
    sub-domain rollup, e.g. ``banking`` also matches ``banking.retail``)."""
    conn = _connect()
    try:
        if domains:
            clauses = []
            params: list = []
            for dom in domains:
                clauses.append('(domain = ? OR domain LIKE ?)')
                params.extend([dom, f"{dom}.%"])
            where = " OR ".join(clauses)
            rows = conn.execute(
                f'SELECT * FROM "tables" WHERE {where} ORDER BY table_name', params
            ).fetchall()
        else:
            rows = conn.execute('SELECT * FROM "tables" ORDER BY table_name').fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def replace_columns(table_id: int, columns: list[dict]) -> None:
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute('DELETE FROM "columns" WHERE table_id = ?', (table_id,))
        cursor.executemany(
            'INSERT INTO "columns" (table_id, name, dtype, profile_json) VALUES (?, ?, ?, ?)',
            [(table_id, c["name"], c["dtype"], c.get("profile_json")) for c in columns],
        )
        conn.commit()
    finally:
        conn.close()


def get_columns(table_id: int) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            'SELECT * FROM "columns" WHERE table_id = ? ORDER BY name', (table_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_mapping(
    table_id: int, column: str, entity_class: str, confidence: float | None, confirmed: bool = False
) -> None:
    conn = _get_db()
    cursor = conn.cursor()
    try:
        now = datetime.now().isoformat()
        cursor.execute(
            'INSERT INTO column_mappings (table_id, "column", entity_class, confirmed,'
            " confidence, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
            ' ON CONFLICT(table_id, "column", entity_class) DO UPDATE SET'
            " confirmed = excluded.confirmed, confidence = excluded.confidence,"
            " updated_at = excluded.updated_at",
            (table_id, column, entity_class, int(confirmed), confidence, now),
        )
        conn.commit()
    finally:
        conn.close()


def list_mappings(table_id: int) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            'SELECT table_id, "column" AS "column", entity_class, confirmed, confidence,'
            ' updated_at FROM column_mappings WHERE table_id = ? ORDER BY "column", entity_class',
            (table_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_mapping_confirmed(table_id: int, column: str, entity_class: str, confirmed: bool) -> int:
    conn = _get_db()
    cursor = conn.cursor()
    try:
        now = datetime.now().isoformat()
        cursor.execute(
            'UPDATE column_mappings SET confirmed = ?, updated_at = ?'
            ' WHERE table_id = ? AND "column" = ? AND entity_class = ?',
            (int(confirmed), now, table_id, column, entity_class),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def clear_mappings(table_id: int, column: str | None = None) -> int:
    conn = _get_db()
    cursor = conn.cursor()
    try:
        if column is not None:
            cursor.execute(
                'DELETE FROM column_mappings WHERE table_id = ? AND "column" = ?',
                (table_id, column),
            )
        else:
            cursor.execute("DELETE FROM column_mappings WHERE table_id = ?", (table_id,))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def delete_table(table_id: int) -> None:
    """Delete a table and its dependent rows (SQLite FK cascade isn't enabled per-connection)."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM column_mappings WHERE table_id = ?", (table_id,))
        cursor.execute('DELETE FROM "columns" WHERE table_id = ?', (table_id,))
        cursor.execute('DELETE FROM "tables" WHERE id = ?', (table_id,))
        conn.commit()
    finally:
        conn.close()


def dump_all() -> dict:
    """Dump every row of the four registry tables, for backup/restore.

    ``column_mappings`` explicitly aliases the quoted ``"column"`` column back
    to ``column`` in the result, matching ``list_mappings``'s existing shape."""
    conn = _connect()
    try:
        return {
            "datasources": [dict(r) for r in conn.execute("SELECT * FROM datasources").fetchall()],
            "tables": [dict(r) for r in conn.execute('SELECT * FROM "tables"').fetchall()],
            "columns": [dict(r) for r in conn.execute('SELECT * FROM "columns"').fetchall()],
            "column_mappings": [
                dict(r) for r in conn.execute(
                    'SELECT table_id, "column" AS "column", entity_class, confirmed,'
                    " confidence, updated_at FROM column_mappings"
                ).fetchall()
            ],
        }
    finally:
        conn.close()


def restore_all(dump: dict) -> None:
    """Wipe the four registry tables and reinsert rows exactly as dumped,
    preserving primary keys so columns/column_mappings foreign keys stay valid."""
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM column_mappings")
        cursor.execute('DELETE FROM "columns"')
        cursor.execute('DELETE FROM "tables"')
        cursor.execute("DELETE FROM datasources")

        for row in dump.get("datasources", []):
            cursor.execute(
                "INSERT INTO datasources (name, type, path_or_dsn, created_at) VALUES (?, ?, ?, ?)",
                (row["name"], row["type"], row["path_or_dsn"], row["created_at"]),
            )
        for row in dump.get("tables", []):
            cursor.execute(
                'INSERT INTO "tables" (id, datasource, table_name, domain, source_file, sheet,'
                " parquet_path, version, row_count, refresh_mode, business_key,"
                " effective_date_column, ingested_at, sha256)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"], row["datasource"], row["table_name"], row["domain"],
                    row["source_file"], row["sheet"], row["parquet_path"], row["version"],
                    row["row_count"], row["refresh_mode"], row["business_key"],
                    row["effective_date_column"], row["ingested_at"], row["sha256"],
                ),
            )
        for row in dump.get("columns", []):
            cursor.execute(
                'INSERT INTO "columns" (table_id, name, dtype, profile_json) VALUES (?, ?, ?, ?)',
                (row["table_id"], row["name"], row["dtype"], row["profile_json"]),
            )
        for row in dump.get("column_mappings", []):
            cursor.execute(
                'INSERT INTO column_mappings (table_id, "column", entity_class, confirmed,'
                " confidence, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row["table_id"], row["column"], row["entity_class"],
                    row["confirmed"], row["confidence"], row["updated_at"],
                ),
            )
        conn.commit()
    finally:
        conn.close()
