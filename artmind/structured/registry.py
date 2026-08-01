"""SQLite CRUD for the structured-store registry tables.

Mirrors ``artmind/jobs.py``'s connection discipline: open via ``db._get_db()``,
commit, close in a ``finally``. ``"column"`` (a ``column_mappings`` column name)
and the ``"tables"``/``"columns"`` table names are double-quoted per
CLAUDE.md's identifier-quoting convention.
"""

import sqlite3
from datetime import datetime

from artmind.db import _get_db


#: What a table's rows denote, which decides where the content belongs and
#: whether the quarantine rule applies. See
#: docs/superpowers/specs/2026-07-25-cross-store-join-model-design.md §4.
#:   instance  -- particular real-world individuals/events, keyed by a business
#:                key; no ingested document asserts them. Home: tables.
#:   lookup    -- members of a controlled vocabulary or code list. Type-level,
#:                but no ingested document asserts them. Home: tables.
#:   normative -- rules, thresholds, obligations or entitlements a document also
#:                states or could state. Home: graph; quarantined if loaded here.
GRAINS = ("instance", "lookup", "normative")

#: The three independently-resumable classification steps (see the design
#: doc's §2/§4 kg_chunk_status parallel) and the run-status vocabulary each
#: tracks on its own {step}_status column.
SEMANTIC_STEPS = ("grain", "bridge", "mapping")
STEP_STATUSES = ("pending", "ok", "failed")


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
    grain: str = "instance",
) -> int:
    """Insert or, on a ``(datasource, domain, table_name)`` conflict, bump
    ``version`` and refresh the mutable fields. Returns the table id.

    ``grain`` is written on insert only. The update path deliberately leaves it
    alone so an operator's confirmed grain survives a re-ingest, the same way
    ``column_mappings`` confirmations do — use ``set_grain`` to change it.
    """
    if grain not in GRAINS:
        raise ValueError(f"grain must be one of {GRAINS}, got {grain!r}")
    conn = _get_db()
    cursor = conn.cursor()
    try:
        now = datetime.now().isoformat()
        existing = cursor.execute(
            'SELECT id, version FROM "tables" WHERE datasource = ? AND domain = ? AND table_name = ?',
            (datasource, domain, table_name),
        ).fetchone()
        if existing:
            table_id, version = existing
            cursor.execute(
                'UPDATE "tables" SET source_file = ?, sheet = ?, parquet_path = ?,'
                " version = ?, row_count = ?, refresh_mode = ?, business_key = ?,"
                " effective_date_column = ?, ingested_at = ?, sha256 = ? WHERE id = ?",
                (
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
                " effective_date_column, grain, ingested_at, sha256)"
                " VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
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
                    grain,
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


def list_tables(
    domains: list[str] | None = None, *, include_ancestors: bool = True
) -> list[dict]:
    """List registered tables, optionally scoped to ``domains``.

    Domain matching is **symmetric** about the corpus hierarchy: a query for
    ``banking`` matches tables at ``banking.retail`` (descendants, as before),
    *and* a query for ``banking.cases`` matches tables registered at the bare
    ``banking`` root (ancestors, new).

    The ancestor half is what makes the structured store discoverable at all.
    Documents carry a genre-scoped domain (``banking.cases``, ``banking.policy``)
    because that is the level an extraction schema lives at; tables carry the
    corpus root, because a table has no genre. Without ancestor matching, the
    documented routing workflow -- take the domains from ``domains-overview``,
    ask ``db list --domain <d>`` for each -- returns empty for every one of
    them and an agent concludes there is no structured store, while six
    populated tables sit at ``banking``.

    ``include_ancestors=False`` restores the old descendant-only behaviour, for
    callers whose correctness depends on the scope being closed downwards --
    ``catalogue.project_catalogue`` wipes ``domain`` plus ``domain.*`` and then
    re-projects whatever this returns, so an ancestor table would be written by
    a wipe that never covers it.
    """
    conn = _connect()
    try:
        if domains:
            clauses = []
            params: list = []
            for dom in domains:
                if include_ancestors:
                    clauses.append("(domain = ? OR domain LIKE ? OR ? LIKE domain || '.%')")
                    params.extend([dom, f"{dom}.%", dom])
                else:
                    clauses.append("(domain = ? OR domain LIKE ?)")
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


def routing_surface(
    domains: list[str] | None = None, entity_class: str | None = None
) -> list[dict]:
    """The cross-store bridge, as one readable structure.

    Everything a query agent needs to decide *whether* to touch the structured
    store and *how* to join its results back to the graph, per table:

    - ``entity_classes`` — the derived class scope (from ``column_mappings``);
      the routing key. Never stored, always derived.
    - ``bridge_columns`` — whose cell *values* seed graph retrieval.
    - ``grain`` / ``refresh_mode`` — the quarantine inputs.

    ``entity_class`` filters to tables whose scope contains it, which is the
    class-first discovery path: "which tables are about CUSTOMER?"

    Reads the registry, so this is the data-dir-local answer. A query-only host
    has no registry DB and reads the equivalent from the Neo4j catalogue
    subgraph instead (``structured/catalogue.py``).
    """
    surface = []
    for table in list_tables(domains):
        mappings = list_mappings(table["id"])
        classes = sorted({m["entity_class"] for m in mappings})
        if entity_class is not None and entity_class not in classes:
            continue
        surface.append({
            "table": table["table_name"],
            "domain": table["domain"],
            "grain": table.get("grain") or "instance",
            "grain_confirmed": bool(table.get("grain_confirmed")),
            "grain_status": table.get("grain_status", "pending"),
            "bridge_status": table.get("bridge_status", "pending"),
            "mapping_status": table.get("mapping_status", "pending"),
            "refresh_mode": table.get("refresh_mode"),
            "row_count": table.get("row_count"),
            "entity_classes": [
                {
                    "entity_class": cls,
                    "confirmed": any(
                        m["entity_class"] == cls and m["confirmed"] for m in mappings
                    ),
                    "columns": sorted(
                        m["column"] for m in mappings if m["entity_class"] == cls
                    ),
                }
                for cls in classes
            ],
            "bridge_columns": [
                {
                    "column": role["column"],
                    "bridge_role": role["bridge_role"],
                    "confirmed": bool(role["confirmed"]),
                }
                for role in list_column_roles(table["id"])
            ],
        })
    return surface


def get_table_by_id(table_id: int) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute('SELECT * FROM "tables" WHERE id = ?', (table_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def set_grain(table_id: int, grain: str, confirmed: bool = True) -> int:
    """Change a table's grain. Returns rows affected (0 = unknown table_id).

    ``confirmed`` defaults to True because the usual caller is an operator
    making a deliberate declaration. Automated proposers pass False, and must
    themselves refuse to overwrite a row whose ``grain_confirmed`` is already
    set — the same guarantee the mapping step gives confirmed mappings.
    """
    if grain not in GRAINS:
        raise ValueError(f"grain must be one of {GRAINS}, got {grain!r}")
    # Normative rows assert things that get superseded, so they need history --
    # a replace-mode table would overwrite the old rule with no record it ever
    # applied, and `--asOf` could never reconstruct what was in force. Checked
    # only on the operator path: a *proposal* of normative against a replace
    # table is precisely the signal worth surfacing for review, so it is allowed
    # to persist unconfirmed.
    if confirmed and grain == "normative":
        table = get_table_by_id(table_id)
        if table is not None and table.get("refresh_mode") != "temporal":
            raise ValueError(
                f"grain 'normative' requires refresh_mode 'temporal', but "
                f"{table['table_name']!r} is {table.get('refresh_mode')!r}. Normative "
                "rows get superseded and need SCD-2 history; re-ingest with "
                "--refreshMode temporal --businessKey <cols> first."
            )
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'UPDATE "tables" SET grain = ?, grain_confirmed = ? WHERE id = ?',
            (grain, int(confirmed), table_id),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def set_step_status(table_id: int, step: str, status: str) -> int:
    """Record whether ``step``'s LLM call was attempted and succeeded since the
    table's last profile change. Returns rows affected (0 = unknown table_id).

    No persisted error text, by design -- a step's failure is only ever
    visible via loguru, mirroring every other best-effort hook in this
    codebase (see ``registry.py``'s module docstring convention).
    """
    if step not in SEMANTIC_STEPS:
        raise ValueError(f"step must be one of {SEMANTIC_STEPS}, got {step!r}")
    if status not in STEP_STATUSES:
        raise ValueError(f"status must be one of {STEP_STATUSES}, got {status!r}")
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(f'UPDATE "tables" SET {step}_status = ? WHERE id = ?', (status, table_id))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def upsert_column_role(
    table_id: int, column: str, bridge_role: str, confidence: float | None, confirmed: bool = False
) -> None:
    """Record that a column's *values* seed graph retrieval during fusion.

    Same proposed→confirmed shape as ``upsert_mapping``. One role per column,
    hence the (table_id, column) key — unlike ``column_mappings``, where a single
    column can legitimately map to several entity classes.
    """
    conn = _get_db()
    cursor = conn.cursor()
    try:
        now = datetime.now().isoformat()
        cursor.execute(
            'INSERT INTO column_roles (table_id, "column", bridge_role, confirmed,'
            " confidence, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
            ' ON CONFLICT(table_id, "column") DO UPDATE SET'
            " bridge_role = excluded.bridge_role, confirmed = excluded.confirmed,"
            " confidence = excluded.confidence, updated_at = excluded.updated_at",
            (table_id, column, bridge_role, int(confirmed), confidence, now),
        )
        conn.commit()
    finally:
        conn.close()


def list_column_roles(table_id: int, confirmed_only: bool = False) -> list[dict]:
    conn = _connect()
    try:
        sql = (
            'SELECT table_id, "column" AS "column", bridge_role, confirmed, confidence,'
            " updated_at FROM column_roles WHERE table_id = ?"
        )
        if confirmed_only:
            sql += " AND confirmed = 1"
        rows = conn.execute(sql + ' ORDER BY "column"', (table_id,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def set_column_role_confirmed(table_id: int, column: str, confirmed: bool) -> int:
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'UPDATE column_roles SET confirmed = ?, updated_at = ?'
            ' WHERE table_id = ? AND "column" = ?',
            (int(confirmed), datetime.now().isoformat(), table_id, column),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def clear_column_roles(table_id: int, column: str | None = None) -> int:
    conn = _get_db()
    cursor = conn.cursor()
    try:
        if column is None:
            cursor.execute("DELETE FROM column_roles WHERE table_id = ?", (table_id,))
        else:
            cursor.execute(
                'DELETE FROM column_roles WHERE table_id = ? AND "column" = ?',
                (table_id, column),
            )
        conn.commit()
        return cursor.rowcount
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
    """Dump every row of the five registry tables, for backup/restore.

    ``column_mappings``/``column_roles`` explicitly alias the quoted ``"column"``
    column back to ``column`` in the result, matching ``list_mappings``'s
    existing shape."""
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
            "column_roles": [
                dict(r) for r in conn.execute(
                    'SELECT table_id, "column" AS "column", bridge_role, confirmed,'
                    " confidence, updated_at FROM column_roles"
                ).fetchall()
            ],
        }
    finally:
        conn.close()


def restore_all(dump: dict) -> None:
    """Wipe the five registry tables and reinsert rows exactly as dumped,
    preserving primary keys so columns/column_mappings/column_roles foreign keys
    stay valid.

    Tolerates dumps taken before ``grain``/``column_roles`` existed: a missing
    grain falls back to the 'instance' default rather than failing the restore.
    """
    conn = _get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM column_roles")
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
                " effective_date_column, grain, grain_confirmed, ingested_at, sha256)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row["id"], row["datasource"], row["table_name"], row["domain"],
                    row["source_file"], row["sheet"], row["parquet_path"], row["version"],
                    row["row_count"], row["refresh_mode"], row["business_key"],
                    row["effective_date_column"], row.get("grain") or "instance",
                    int(row.get("grain_confirmed") or 0),
                    row["ingested_at"], row["sha256"],
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
        for row in dump.get("column_roles", []):
            cursor.execute(
                'INSERT INTO column_roles (table_id, "column", bridge_role, confirmed,'
                " confidence, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row["table_id"], row["column"], row["bridge_role"],
                    row["confirmed"], row["confidence"], row["updated_at"],
                ),
            )
        conn.commit()
    finally:
        conn.close()
