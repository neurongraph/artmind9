import csv

import pytest

pytest.importorskip("duckdb")


def _patch_stores(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    monkeypatch.setattr(paths, "STRUCTURED_SNAPSHOT_DIR", tmp_path / "structured_snapshot")
    db._init_db()


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def test_export_then_import_round_trips_parquet_and_registry(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import shutil

    import paths
    from artmind.structured import registry
    from artmind.structured.duckdb_adapter import DuckDBDatasource
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured_snapshot import export_structured, import_structured

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"], [2, "Gadget"]])
    ingest_structured_file(csv_path, "banking")

    snapshot_path = export_structured()
    assert snapshot_path.exists()

    # simulate total loss of the structured store
    shutil.rmtree(paths.STRUCTURED_DIR)
    table = registry.get_table("products", domain="banking")
    registry.delete_table(table["id"])
    assert registry.get_table("products", domain="banking") is None

    summary = import_structured(snapshot_path)
    assert summary["table_count"] == 1

    restored = registry.get_table("products", domain="banking")
    assert restored is not None
    assert restored["row_count"] == 2

    ds = DuckDBDatasource()
    ds.ensure_views(registry.list_tables())
    rows = ds.run_sql("SELECT count(*) AS n FROM products")
    assert rows == [{"n": 2}]


def test_import_uses_latest_when_path_omitted(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured_snapshot import export_structured, import_structured

    csv_path = tmp_path / "a.csv"
    _write_csv(csv_path, [["id"], [1]])
    ingest_structured_file(csv_path, "general")
    export_structured()

    summary = import_structured()  # no path -> latest in STRUCTURED_SNAPSHOT_DIR
    assert summary["table_count"] == 1


def test_import_no_snapshots_raises(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured_snapshot import import_structured

    with pytest.raises(FileNotFoundError):
        import_structured()
