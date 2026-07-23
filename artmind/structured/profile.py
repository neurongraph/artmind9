"""Per-column profiling: null rate, plus numeric min/max or categorical sampling.

Column identifiers are quoted (``"<col>"``) to survive spaces or reserved words.
``column`` in particular comes straight from ``introspect_schema()`` — i.e.
whatever header DuckDB parsed out of an uploaded CSV/XLSX — so it is untrusted
and may itself contain a literal ``"``. Both ``column`` and ``table`` are run
through ``_quote_ident`` (doubling embedded ``"``, mirroring
``_sql_quote_literal``'s ``'``-doubling in ``duckdb_adapter.py``) before being
interpolated into SQL, so a crafted header can't break out of the quoted
identifier and inject arbitrary SQL.
"""

from artmind.structured.connector import Profile

_NUMERIC_MARKERS = ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL", "HUGEINT")


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _is_numeric_dtype(dtype: str) -> bool:
    upper = dtype.upper()
    return any(marker in upper for marker in _NUMERIC_MARKERS)


def profile_column(
    con,
    table: str,
    column: str,
    dtype: str,
    *,
    sample_size: int = 25,
    categorical_max: int = 200,
) -> Profile:
    qcol = _quote_ident(column)
    qtable = _quote_ident(table)

    null_rate = con.execute(
        f"SELECT avg(CASE WHEN {qcol} IS NULL THEN 1 ELSE 0 END) FROM {qtable}"
    ).fetchone()[0]

    if _is_numeric_dtype(dtype):
        minimum, maximum = con.execute(
            f"SELECT min({qcol}), max({qcol}) FROM {qtable}"
        ).fetchone()
        return Profile(
            kind="numeric",
            distinct_sample=[],
            cardinality=None,
            minimum=minimum,
            maximum=maximum,
            null_rate=null_rate,
        )

    cardinality = con.execute(
        f"SELECT count(DISTINCT {qcol}) FROM {qtable}"
    ).fetchone()[0]

    if cardinality <= categorical_max:
        rows = con.execute(
            f"SELECT DISTINCT {qcol} FROM {qtable} WHERE {qcol} IS NOT NULL"
            f" LIMIT {int(sample_size)}"
        ).fetchall()
        distinct_sample = [r[0] for r in rows]
        return Profile(
            kind="categorical",
            distinct_sample=distinct_sample,
            cardinality=cardinality,
            null_rate=null_rate,
        )

    return Profile(
        kind="other",
        distinct_sample=[],
        cardinality=cardinality,
        null_rate=null_rate,
    )
