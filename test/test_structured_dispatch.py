import csv

import pytest

pytest.importorskip("openpyxl")


def _patch_stores(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    db._init_db()


def _write_csv(path, rows):
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(rows)


def test_ingest_structured_file_creates_table_and_columns(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file
    from artmind.structured import registry

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"], [2, "Gadget"]])

    result = ingest_structured_file(csv_path, "banking")
    assert result["status"] == "ok"
    assert len(result["tables"]) == 1
    entry = result["tables"][0]
    assert entry["table_name"] == "products"
    assert entry["row_count"] == 2
    assert entry["version"] == 1

    from pathlib import Path

    assert Path(entry["parquet_path"]).exists()

    table_row = registry.get_table("products", domain="banking")
    columns = registry.get_columns(table_row["id"])
    assert {c["name"] for c in columns} == {"id", "name"}


def test_ingest_structured_file_dedup_skip_and_force(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"]])

    first = ingest_structured_file(csv_path, "banking")
    assert first["status"] == "ok"
    assert first["tables"][0]["version"] == 1

    second = ingest_structured_file(csv_path, "banking")
    assert second["status"] == "skipped"

    forced = ingest_structured_file(csv_path, "banking", force=True)
    assert forced["status"] == "ok"
    assert forced["tables"][0]["version"] == 2


def test_ingest_structured_file_messy_header_errors(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    import click
    from artmind.structured.pipeline import ingest_structured_file

    csv_path = tmp_path / "messy.csv"
    _write_csv(csv_path, [["id", "", "name"], [1, "x", "Widget"]])

    with pytest.raises(click.ClickException):
        ingest_structured_file(csv_path, "banking")


def test_refresh_table_bumps_version(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file, refresh_table

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"]])
    ingest_structured_file(csv_path, "banking")

    _write_csv(csv_path, [["id", "name"], [1, "Widget"], [2, "Gadget"]])
    result = refresh_table("products", "banking")
    assert result["status"] == "ok"
    assert result["row_count"] == 2
    assert result["version"] == 2


def test_refresh_table_unregistered_raises(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import refresh_table

    with pytest.raises(ValueError):
        refresh_table("nope", "banking")
