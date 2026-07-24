"""SCD-2 (slowly changing dimension, type 2) refresh for temporal structured tables.

A "temporal" structured table keeps its full history in one parquet file,
tagged with three system columns: ``_valid_from``, ``_valid_to`` (``NULL`` =
still open), ``_is_current`` (bool). ``apply_scd2_refresh`` diffs a new batch
of rows against that history and rewrites the file with the merged result;
``asof_view_sql`` builds the read-side query fragment (current-only, or as of
a given date) mirroring ``graph_query.asof_predicate``'s valid-time semantics.

DuckDB cannot ``UPDATE``/``INSERT`` a parquet-backed VIEW in place, so the
refresh materializes the existing history into a real temp table, applies the
diff there, then ``COPY``s the full history back to the parquet file. Views
over ``read_parquet(...)`` re-read the file lazily on every query (verified:
overwriting the file is visible to an already-open VIEW on the same
connection, and to a VIEW created fresh over the same path by a different
connection, with no need to recreate the VIEW) -- so this module never needs
to touch a VIEW itself; that stays owned by ``duckdb_adapter``.
"""

from pathlib import Path

import duckdb

#: The three columns every temporal table's parquet carries in addition to
#: its business-key and regular data columns.
SYSTEM_COLUMNS = ("_valid_from", "_valid_to", "_is_current")


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _date_literal(value: str) -> str:
    return f"DATE {_quote_literal(value)}"


def apply_scd2_refresh(
    con: duckdb.DuckDBPyConnection,
    table: Path | str,
    incoming_rel: str,
    business_key: list[str],
    effective_date: str,
    *,
    effective_date_column: str | None = None,
) -> dict:
    """Diff ``incoming_rel`` against a temporal table's history and rewrite it.

    ``table`` is the path to the parquet file backing this temporal table's
    full history (e.g. ``duckdb_adapter.parquet_path_for(domain, name)``) --
    **not** a catalog view/table name. The file must already exist and carry
    ``business_key`` + regular data columns + the three ``SYSTEM_COLUMNS``
    (seeding a brand-new temporal table with its first batch is the caller's
    job, not this function's).

    ``incoming_rel`` is a SQL relation expression usable directly after
    ``FROM`` -- a bare table/view name already registered on ``con`` (e.g. a
    staged temp table holding the new batch), or a parenthesized subquery. It
    is interpolated directly into the generated SQL, so callers must not pass
    unsanitized/untrusted text. Its rows must carry exactly the business-key
    and regular (non-system) columns of the table, by name.

    ``business_key`` is the list of column names that identify the same
    logical row across refreshes; at least one column is required, and none
    may collide with ``SYSTEM_COLUMNS``. ``effective_date`` is the batch/
    fallback date (ISO string) used as the closing date for rows closed in
    this batch, and as the fallback opening date for new/changed rows when
    ``effective_date_column`` is not set or is null on a given row.

    Column-name equality on the business key uses ``IS NOT DISTINCT FROM``
    (NULL-safe) rather than ``=``, so composite keys with NULL parts still
    match correctly -- a plain ``=`` join would silently treat two NULL key
    parts as non-matching and re-open every such row on every refresh.

    Returns ``{"inserted": int, "closed": int, "unchanged": int}``.
    """
    if not business_key:
        raise ValueError("business_key must contain at least one column")
    collision = sorted(set(business_key) & set(SYSTEM_COLUMNS))
    if collision:
        raise ValueError(f"business_key columns collide with system columns: {collision}")

    parquet_literal = _quote_literal(table)

    con.execute(f"CREATE OR REPLACE TEMP TABLE _cur AS SELECT * FROM read_parquet({parquet_literal})")

    cur_columns = [row[0] for row in con.execute("DESCRIBE _cur").fetchall()]
    missing_bk = [c for c in business_key if c not in cur_columns]
    if missing_bk:
        raise ValueError(f"business_key columns not present in table: {missing_bk}")

    regular_columns = [c for c in cur_columns if c not in business_key and c not in SYSTEM_COLUMNS]
    nonkey_row_expr = (
        "ROW(" + ", ".join(_quote_ident(c) for c in regular_columns) + ")"
        if regular_columns
        else "ROW(1)"  # no regular columns: every row hashes equal, so only key presence/absence matters
    )

    eff_expr = (
        f"COALESCE({_quote_ident(effective_date_column)}, {_date_literal(effective_date)})"
        if effective_date_column
        else _date_literal(effective_date)
    )

    con.execute(
        "CREATE OR REPLACE TEMP TABLE _inc AS "
        f"SELECT *, hash({nonkey_row_expr}) AS _h, {eff_expr} AS _eff FROM {incoming_rel}"
    )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _cur_open AS "
        f"SELECT *, hash({nonkey_row_expr}) AS _h FROM _cur WHERE _is_current"
    )

    bk_ident = [_quote_ident(c) for c in business_key]
    match_i_c = " AND ".join(f"i.{c} IS NOT DISTINCT FROM c.{c}" for c in bk_ident)
    match_c_cur = " AND ".join(f"c.{c} IS NOT DISTINCT FROM _cur.{c}" for c in bk_ident)

    total_open_before = con.execute("SELECT count(*) FROM _cur_open").fetchone()[0]

    # Close every currently-open row that has no incoming row with the
    # identical (business key, non-key hash) pair -- i.e. its key changed
    # content, or its key disappeared from this batch entirely.
    closed_rows = con.execute(
        f"""
        UPDATE _cur SET _valid_to = {_date_literal(effective_date)}, _is_current = false
        WHERE _is_current
          AND NOT EXISTS (
            SELECT 1 FROM _inc i JOIN _cur_open c ON {match_i_c}
            WHERE {match_c_cur} AND i._h = c._h
          )
        RETURNING 1
        """
    ).fetchall()
    closed = len(closed_rows)

    # Open a new version for every incoming row whose (business key, hash)
    # pair is not already open -- new keys, and changed keys (whose stale
    # version was just closed above). Rows whose key + hash are unchanged are
    # excluded here, leaving them untouched (no-op).
    inserted_rows = con.execute(
        f"""
        INSERT INTO _cur BY NAME
        SELECT i.* EXCLUDE (_h, _eff), i._eff AS _valid_from, NULL AS _valid_to, true AS _is_current
        FROM _inc i
        WHERE NOT EXISTS (
          SELECT 1 FROM _cur_open c WHERE {match_i_c} AND i._h = c._h
        )
        RETURNING 1
        """
    ).fetchall()
    inserted = len(inserted_rows)

    unchanged = total_open_before - closed

    con.execute(f"COPY _cur TO {parquet_literal} (FORMAT PARQUET)")

    return {"inserted": inserted, "closed": closed, "unchanged": unchanged}


def asof_view_sql(table: str, as_of: str | None = None) -> str:
    """Build a self-contained, directly-executable ``SELECT`` for a temporal table.

    ``table`` is the name of an existing catalog table/view exposing the full
    SCD-2 history (e.g. the VIEW ``duckdb_adapter._create_view`` creates over
    a temporal table's parquet). This returns a bare
    ``SELECT * FROM <table> WHERE ...`` string -- not a ``CREATE VIEW``
    statement -- so a caller can run it directly (``con.execute(sql)``) or
    embed it as a subquery/CTE. To materialize a persistent
    ``<table>_current`` view instead, wrap the result yourself, e.g.
    ``f"CREATE VIEW {table}_current AS {asof_view_sql(table)}"``.

    ``as_of=None`` returns the "current" filter (``_is_current``). A given
    ``as_of`` (an ISO date string) returns the as-of filter using the same
    valid-time convention as ``graph_query.asof_predicate``: a row is visible
    when ``_valid_from <= as_of`` and (``_valid_to`` is still open, or
    ``as_of`` falls strictly before it). Resolve/validate ``as_of`` (e.g. via
    ``graph_query.resolve_as_of``) before calling this function -- it is
    interpolated directly into the SQL as a DATE literal.
    """
    ident = _quote_ident(table)
    if as_of is None:
        return f'SELECT * FROM {ident} WHERE "_is_current"'
    date_lit = _date_literal(as_of)
    return (
        f'SELECT * FROM {ident} WHERE "_valid_from" <= {date_lit} '
        f'AND ("_valid_to" IS NULL OR {date_lit} < "_valid_to")'
    )
