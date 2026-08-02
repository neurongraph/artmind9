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


def test_import_restores_step_status_and_bridge_columns(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import shutil

    import paths
    from artmind.structured import registry
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured_snapshot import export_structured, import_structured

    csv_path = tmp_path / "complaints.csv"
    _write_csv(csv_path, [["id", "category"], [1, "Fee Dispute"], [2, "Fraud"]])
    ingest_structured_file(csv_path, "banking")

    table = registry.get_table("complaints", domain="banking")
    registry.set_step_status(table["id"], "grain", "ok")
    registry.set_step_status(table["id"], "bridge", "ok")
    registry.set_step_status(table["id"], "mapping", "failed")
    registry.upsert_column_role(table["id"], "category", "term", 0.9, confirmed=True)

    snapshot_path = export_structured()

    shutil.rmtree(paths.STRUCTURED_DIR)
    registry.delete_table(table["id"])
    assert registry.get_table("complaints", domain="banking") is None

    import_structured(snapshot_path)

    restored = registry.get_table("complaints", domain="banking")
    assert restored is not None
    assert restored["grain_status"] == "ok"
    assert restored["bridge_status"] == "ok"
    assert restored["mapping_status"] == "failed"

    roles = registry.list_column_roles(restored["id"])
    assert roles == [
        {
            "table_id": restored["id"],
            "column": "category",
            "bridge_role": "term",
            "confirmed": 1,
            "confidence": 0.9,
            "updated_at": roles[0]["updated_at"],
        }
    ]


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
