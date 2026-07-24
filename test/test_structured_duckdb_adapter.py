import csv

import pytest

openpyxl = pytest.importorskip("openpyxl")


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)


def test_load_table_from_csv_row_count_and_schema(tmp_path, monkeypatch):
    import paths
    from artmind.structured.duckdb_adapter import DuckDBDatasource

    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")

    csv_path = tmp_path / "products.csv"
    _write_csv(
        csv_path,
        [
            ["id", "name", "price"],
            [1, "Widget", 9.99],
            [2, "Gadget", 19.99],
            [3, "Gizmo", 29.99],
        ],
    )

    ds = DuckDBDatasource(db_path=tmp_path / "structured" / "artmind.duckdb")
    row_count = ds.load_table(csv_path, "products", "banking")
    assert row_count == 3

    schema = ds.introspect_schema("products")
    names = {c.name for c in schema}
    assert names == {"id", "name", "price"}

    rows = ds.run_sql("SELECT count(*) AS n FROM products")
    assert rows == [{"n": 3}]

    parquet = paths.STRUCTURED_DIR / "banking" / "products.parquet"
    assert parquet.exists()


def test_load_table_from_xlsx_sheet(tmp_path, monkeypatch):
    import paths
    from artmind.structured.duckdb_adapter import DuckDBDatasource

    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")

    xlsx_path = tmp_path / "accounts.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["account_id", "balance"])
    ws.append(["A1", 100.5])
    ws.append(["A2", 250.0])
    wb.save(xlsx_path)

    ds = DuckDBDatasource(db_path=tmp_path / "structured" / "artmind.duckdb")
    row_count = ds.load_table(xlsx_path, "accounts", "banking", sheet="Sheet1")
    assert row_count == 2

    rows = ds.run_sql("SELECT account_id, balance FROM accounts ORDER BY account_id")
    assert rows == [
        {"account_id": "A1", "balance": 100.5},
        {"account_id": "A2", "balance": 250.0},
    ]


def test_ensure_views_recreates_view_on_fresh_connection(tmp_path, monkeypatch):
    import paths
    from artmind.structured.duckdb_adapter import DuckDBDatasource, parquet_path_for

    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"]])

    db_path = tmp_path / "structured" / "artmind.duckdb"
    ds = DuckDBDatasource(db_path=db_path)
    ds.load_table(csv_path, "products", "banking")
    ds.con.close()

    fresh = DuckDBDatasource(db_path=db_path)
    fresh.ensure_views(
        [
            {
                "table_name": "products",
                "domain": "banking",
                "parquet_path": str(parquet_path_for("banking", "products")),
            }
        ]
    )
    rows = fresh.run_sql("SELECT count(*) AS n FROM products")
    assert rows == [{"n": 1}]
    rows = fresh.run_sql("SELECT count(*) AS n FROM banking__products")
    assert rows == [{"n": 1}]


def test_in_memory_instances_do_not_share_views(tmp_path, monkeypatch):
    """Two separate ``DuckDBDatasource.in_memory()`` connections must not share
    catalog state — each ``duckdb.connect(':memory:')`` call opens its own
    private, ephemeral database. This is the structural property text2sql's
    domain-scoped execution relies on for isolation."""
    import paths
    from artmind.structured.duckdb_adapter import DuckDBDatasource, parquet_path_for

    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"]])
    persistent = DuckDBDatasource(db_path=tmp_path / "structured" / "artmind.duckdb")
    persistent.load_table(csv_path, "products", "banking")

    first = DuckDBDatasource.in_memory()
    first.ensure_views(
        [
            {
                "table_name": "products",
                "domain": "banking",
                "parquet_path": str(parquet_path_for("banking", "products")),
            }
        ]
    )
    assert first.run_sql("SELECT count(*) AS n FROM products") == [{"n": 1}]

    second = DuckDBDatasource.in_memory()
    with pytest.raises(Exception):
        second.run_sql("SELECT count(*) AS n FROM products")


def test_in_memory_instance_does_not_touch_persistent_db_file(tmp_path, monkeypatch):
    """An in-memory instance must never create or write the shared persistent
    ``structured_db_path()`` file — its whole purpose is to avoid touching
    that shared catalog at all."""
    import paths
    from artmind.structured.duckdb_adapter import DuckDBDatasource, structured_db_path

    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")

    persistent_path = structured_db_path()
    assert not persistent_path.exists()

    ds = DuckDBDatasource.in_memory()
    ds.con.execute("CREATE TABLE t AS SELECT 1 AS x")
    assert ds.run_sql("SELECT * FROM t") == [{"x": 1}]

    assert not persistent_path.exists()
    assert not persistent_path.parent.exists()
