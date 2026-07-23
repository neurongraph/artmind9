"""Per-column profiling: null rate, plus numeric min/max or categorical sampling.

Column identifiers are quoted (``"<col>"``) to survive spaces or reserved words;
the table name is a trusted, already-registered identifier (see
``artmind/structured/duckdb_adapter.py`` for the same discipline).
"""

from artmind.structured.connector import Profile

_NUMERIC_MARKERS = ("INT", "DOUBLE", "FLOAT", "DECIMAL", "NUMERIC", "REAL", "HUGEINT")


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
    null_rate = con.execute(
        f'SELECT avg(CASE WHEN "{column}" IS NULL THEN 1 ELSE 0 END) FROM "{table}"'
    ).fetchone()[0]

    if _is_numeric_dtype(dtype):
        minimum, maximum = con.execute(
            f'SELECT min("{column}"), max("{column}") FROM "{table}"'
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
        f'SELECT count(DISTINCT "{column}") FROM "{table}"'
    ).fetchone()[0]

    if cardinality <= categorical_max:
        rows = con.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'
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
