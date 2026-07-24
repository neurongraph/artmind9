import datetime

import duckdb
import pytest

from artmind.structured.scd2 import apply_scd2_refresh, asof_view_sql


def _seed_history(con, parquet_path, rows):
    """Write an initial SCD-2 history (rows already carry system columns)."""
    con.execute("CREATE OR REPLACE TEMP TABLE _seed AS " + rows)
    con.execute(f"COPY _seed TO '{parquet_path}' (FORMAT PARQUET)")


@pytest.fixture
def con():
    c = duckdb.connect(":memory:")
    yield c
    c.close()


def _read_history(con, parquet_path):
    return con.execute(
        f"SELECT * FROM read_parquet('{parquet_path}') ORDER BY id, _valid_from"
    ).fetchall()


def test_four_cases_unchanged_changed_new_disappeared(con, tmp_path):
    parquet_path = tmp_path / "products.parquet"

    # Seed: 3 current rows.
    # id=1 -> unchanged in the incoming batch
    # id=2 -> changed (amount differs)
    # id=3 -> disappeared (absent from incoming batch)
    _seed_history(
        con,
        str(parquet_path),
        """
        SELECT * FROM (VALUES
          (1, 'A', 10, DATE '2024-01-01', CAST(NULL AS DATE), true),
          (2, 'B', 20, DATE '2024-01-01', CAST(NULL AS DATE), true),
          (3, 'C', 30, DATE '2024-01-01', CAST(NULL AS DATE), true)
        ) AS t(id, name, amount, _valid_from, _valid_to, _is_current)
        """,
    )

    # Incoming batch: id=1 unchanged, id=2 changed, id=4 new key.
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE incoming AS
        SELECT * FROM (VALUES
          (1, 'A', 10),
          (2, 'B', 25),
          (4, 'D', 40)
        ) AS t(id, name, amount)
        """
    )

    result = apply_scd2_refresh(
        con,
        str(parquet_path),
        "incoming",
        business_key=["id"],
        effective_date="2024-06-01",
    )

    assert result == {"inserted": 2, "closed": 2, "unchanged": 1}

    history = {(r[0], r[1], r[2], r[3], r[4], r[5]) for r in _read_history(con, parquet_path)}
    assert history == {
        # id=1: unchanged, still open at its original valid_from.
        (1, "A", 10, datetime.date(2024, 1, 1), None, True),
        # id=2: old version closed at the batch effective date...
        (2, "B", 20, datetime.date(2024, 1, 1), datetime.date(2024, 6, 1), False),
        # ...and a new open version starting at the batch effective date.
        (2, "B", 25, datetime.date(2024, 6, 1), None, True),
        # id=3: disappeared -> closed, never re-inserted.
        (3, "C", 30, datetime.date(2024, 1, 1), datetime.date(2024, 6, 1), False),
        # id=4: brand new key -> inserted as an open row.
        (4, "D", 40, datetime.date(2024, 6, 1), None, True),
    }


def test_unchanged_row_is_a_true_noop_no_row_rewritten(con, tmp_path):
    """An unchanged row must not gain a new _valid_from -- it's untouched, not
    closed-and-reopened."""
    parquet_path = tmp_path / "t.parquet"
    _seed_history(
        con,
        str(parquet_path),
        """
        SELECT * FROM (VALUES
          (1, 'A', DATE '2020-05-05', CAST(NULL AS DATE), true)
        ) AS t(id, name, _valid_from, _valid_to, _is_current)
        """,
    )
    con.execute("CREATE OR REPLACE TEMP TABLE incoming AS SELECT 1 AS id, 'A' AS name")

    result = apply_scd2_refresh(
        con, str(parquet_path), "incoming", business_key=["id"], effective_date="2024-06-01"
    )
    assert result == {"inserted": 0, "closed": 0, "unchanged": 1}

    rows = _read_history(con, parquet_path)
    assert len(rows) == 1
    assert rows[0][2] == datetime.date(2020, 5, 5)  # _valid_from untouched
    assert rows[0][3] is None
    assert rows[0][4] is True


def test_composite_business_key_with_null_part_is_null_safe(con, tmp_path):
    """A composite key with a NULL part must still be recognized as the same
    logical row across refreshes (IS NOT DISTINCT FROM, not '=')."""
    parquet_path = tmp_path / "composite.parquet"
    _seed_history(
        con,
        str(parquet_path),
        """
        SELECT * FROM (VALUES
          (1, CAST(NULL AS VARCHAR), 'v1', DATE '2024-01-01', CAST(NULL AS DATE), true)
        ) AS t(id, region, value, _valid_from, _valid_to, _is_current)
        """,
    )
    # Same key (id=1, region=NULL), unchanged value.
    con.execute(
        "CREATE OR REPLACE TEMP TABLE incoming AS "
        "SELECT 1 AS id, CAST(NULL AS VARCHAR) AS region, 'v1' AS value"
    )

    result = apply_scd2_refresh(
        con,
        str(parquet_path),
        "incoming",
        business_key=["id", "region"],
        effective_date="2024-06-01",
    )
    assert result == {"inserted": 0, "closed": 0, "unchanged": 1}
    rows = _read_history(con, parquet_path)
    assert len(rows) == 1
    assert rows[0][5] is True  # still current, untouched


def test_effective_date_column_drives_new_row_valid_from_not_batch_date(con, tmp_path):
    """When effective_date_column is set, the new open row's _valid_from comes
    from that column, while the closed row's _valid_to still uses the batch
    effective_date (per the plan's explicit distinction)."""
    parquet_path = tmp_path / "eff.parquet"
    _seed_history(
        con,
        str(parquet_path),
        """
        SELECT * FROM (VALUES
          (1, 10, DATE '2024-01-01', DATE '2024-01-01', CAST(NULL AS DATE), true)
        ) AS t(id, amount, report_date, _valid_from, _valid_to, _is_current)
        """,
    )
    con.execute(
        "CREATE OR REPLACE TEMP TABLE incoming AS "
        "SELECT 1 AS id, 99 AS amount, DATE '2024-03-15' AS report_date"
    )

    result = apply_scd2_refresh(
        con,
        str(parquet_path),
        "incoming",
        business_key=["id"],
        effective_date="2024-06-01",
        effective_date_column="report_date",
    )
    assert result == {"inserted": 1, "closed": 1, "unchanged": 0}

    rows = con.execute(
        f"SELECT id, amount, _valid_from, _valid_to, _is_current FROM read_parquet('{parquet_path}') "
        "ORDER BY _valid_from"
    ).fetchall()
    assert rows[0] == (1, 10, datetime.date(2024, 1, 1), datetime.date(2024, 6, 1), False)
    assert rows[1] == (1, 99, datetime.date(2024, 3, 15), None, True)


def test_missing_business_key_column_raises(con, tmp_path):
    parquet_path = tmp_path / "t.parquet"
    _seed_history(
        con,
        str(parquet_path),
        """
        SELECT * FROM (VALUES (1, DATE '2024-01-01', CAST(NULL AS DATE), true))
        AS t(id, _valid_from, _valid_to, _is_current)
        """,
    )
    con.execute("CREATE OR REPLACE TEMP TABLE incoming AS SELECT 1 AS id")

    with pytest.raises(ValueError, match="not present in table"):
        apply_scd2_refresh(
            con, str(parquet_path), "incoming", business_key=["nope"], effective_date="2024-06-01"
        )


def test_business_key_colliding_with_system_column_raises(con, tmp_path):
    with pytest.raises(ValueError, match="collide with system columns"):
        apply_scd2_refresh(
            con,
            str(tmp_path / "t.parquet"),
            "incoming",
            business_key=["_is_current"],
            effective_date="2024-06-01",
        )


def test_asof_view_sql_current_returns_exactly_current_set(con, tmp_path):
    parquet_path = tmp_path / "hist.parquet"
    _seed_history(
        con,
        str(parquet_path),
        """
        SELECT * FROM (VALUES
          (1, 'A', DATE '2024-01-01', CAST(NULL AS DATE), true),
          (2, 'B', DATE '2024-01-01', DATE '2024-06-01', false),
          (2, 'B2', DATE '2024-06-01', CAST(NULL AS DATE), true)
        ) AS t(id, name, _valid_from, _valid_to, _is_current)
        """,
    )
    con.execute(f"CREATE VIEW hist AS SELECT * FROM read_parquet('{parquet_path}')")

    sql = asof_view_sql("hist")
    rows = con.execute(sql).fetchall()
    ids_names = {(r[0], r[1]) for r in rows}
    assert ids_names == {(1, "A"), (2, "B2")}
    assert len(rows) == 2


def test_asof_view_sql_mid_history_returns_then_current_versions(con, tmp_path):
    parquet_path = tmp_path / "hist2.parquet"
    _seed_history(
        con,
        str(parquet_path),
        """
        SELECT * FROM (VALUES
          (1, 'A', DATE '2024-01-01', CAST(NULL AS DATE), true),
          (2, 'B', DATE '2024-01-01', DATE '2024-06-01', false),
          (2, 'B2', DATE '2024-06-01', CAST(NULL AS DATE), true)
        ) AS t(id, name, _valid_from, _valid_to, _is_current)
        """,
    )
    con.execute(f"CREATE VIEW hist2 AS SELECT * FROM read_parquet('{parquet_path}')")

    # As of a date before id=2's change: should see the original 'B', not 'B2'.
    sql = asof_view_sql("hist2", as_of="2024-03-01")
    rows = con.execute(sql).fetchall()
    ids_names = {(r[0], r[1]) for r in rows}
    assert ids_names == {(1, "A"), (2, "B")}

    # Exactly at the boundary date: _valid_to is exclusive on the old side
    # (as_of < _valid_to fails when as_of == _valid_to), so the old row is no
    # longer visible, and the new row is visible from its _valid_from onward.
    sql_boundary = asof_view_sql("hist2", as_of="2024-06-01")
    rows_boundary = con.execute(sql_boundary).fetchall()
    ids_names_boundary = {(r[0], r[1]) for r in rows_boundary}
    assert ids_names_boundary == {(1, "A"), (2, "B2")}


def test_disappeared_key_is_closed_and_never_reinserted(con, tmp_path):
    parquet_path = tmp_path / "gone.parquet"
    _seed_history(
        con,
        str(parquet_path),
        """
        SELECT * FROM (VALUES (1, 'A', DATE '2024-01-01', CAST(NULL AS DATE), true))
        AS t(id, name, _valid_from, _valid_to, _is_current)
        """,
    )
    con.execute("CREATE OR REPLACE TEMP TABLE incoming AS SELECT 2 AS id, 'X' AS name WHERE false")

    result = apply_scd2_refresh(
        con, str(parquet_path), "incoming", business_key=["id"], effective_date="2024-06-01"
    )
    assert result == {"inserted": 0, "closed": 1, "unchanged": 0}

    rows = con.execute(
        f"SELECT id, _is_current FROM read_parquet('{parquet_path}')"
    ).fetchall()
    assert rows == [(1, False)]


def test_full_history_is_preserved_across_multiple_refreshes(con, tmp_path):
    """The COPY at the end must write back the complete history (old closed
    rows + still-open rows + newly opened rows), not just the delta."""
    parquet_path = tmp_path / "multi.parquet"
    _seed_history(
        con,
        str(parquet_path),
        """
        SELECT * FROM (VALUES (1, 100, DATE '2024-01-01', CAST(NULL AS DATE), true))
        AS t(id, amount, _valid_from, _valid_to, _is_current)
        """,
    )

    con.execute("CREATE OR REPLACE TEMP TABLE batch1 AS SELECT 1 AS id, 200 AS amount")
    apply_scd2_refresh(con, str(parquet_path), "batch1", business_key=["id"], effective_date="2024-03-01")

    con.execute("CREATE OR REPLACE TEMP TABLE batch2 AS SELECT 1 AS id, 300 AS amount")
    apply_scd2_refresh(con, str(parquet_path), "batch2", business_key=["id"], effective_date="2024-06-01")

    rows = con.execute(
        f"SELECT amount, _is_current FROM read_parquet('{parquet_path}') ORDER BY _valid_from"
    ).fetchall()
    assert rows == [(100, False), (200, False), (300, True)]
