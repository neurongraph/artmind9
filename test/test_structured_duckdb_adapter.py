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
        [{"table_name": "products", "parquet_path": str(parquet_path_for("banking", "products"))}]
    )
    rows = fresh.run_sql("SELECT count(*) AS n FROM products")
    assert rows == [{"n": 1}]
