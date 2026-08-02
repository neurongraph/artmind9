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

    import json

    columns_by_name = {c["name"]: c for c in columns}
    assert all(c["profile_json"] for c in columns)
    id_profile = json.loads(columns_by_name["id"]["profile_json"])
    assert id_profile["kind"] == "numeric"
    assert id_profile["minimum"] == 1
    assert id_profile["maximum"] == 2
    name_profile = json.loads(columns_by_name["name"]["profile_json"])
    assert name_profile["kind"] == "categorical"
    assert name_profile["cardinality"] == 2
    assert set(name_profile["distinct_sample"]) == {"Widget", "Gadget"}


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


def test_ingest_structured_file_attempts_classification_on_first_registration(tmp_path, monkeypatch):
    """Superseded by propose_table_semantics wiring: first registration drives
    classification through propose_table_semantics (grain+bridge+mapping),
    not a direct propose_mappings call."""
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file
    import artmind.structured.semantics as semantics

    spy = {"called": False}

    def fake_propose_table_semantics(table_id, domain, **kwargs):
        spy["called"] = True
        spy["table_id"] = table_id
        spy["domain"] = domain
        spy["steps"] = kwargs.get("steps")
        return {"table": "products", "domain": domain}

    monkeypatch.setattr(semantics, "propose_table_semantics", fake_propose_table_semantics)

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"], [2, "Gadget"]])

    result = ingest_structured_file(csv_path, "banking")

    from artmind.structured import registry

    expected_table_id = registry.get_table("products", domain="banking")["id"]

    assert result["status"] == "ok"
    assert spy["called"]
    assert spy["domain"] == "banking"
    assert spy["table_id"] == expected_table_id
    assert set(spy["steps"]) == {"grain", "bridge", "mapping"}


def test_ingest_structured_file_classification_failure_is_non_fatal(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file
    import artmind.structured.semantics as semantics

    def failing_propose_table_semantics(table_id, domain, **kwargs):
        raise RuntimeError("graph is down")

    monkeypatch.setattr(semantics, "propose_table_semantics", failing_propose_table_semantics)

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"]])

    result = ingest_structured_file(csv_path, "banking")

    assert result["status"] == "ok"


def test_ingest_structured_file_projects_catalogue(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file
    import artmind.structured.catalogue as catalogue

    spy = {"calls": []}

    def fake_project_catalogue(domain):
        spy["calls"].append(domain)
        return {"tables": 1, "columns": 2, "mappings": 0}

    monkeypatch.setattr(catalogue, "project_catalogue", fake_project_catalogue)

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"], [2, "Gadget"]])

    result = ingest_structured_file(csv_path, "banking")

    assert result["status"] == "ok"
    assert spy["calls"] == ["banking"]


def test_ingest_structured_file_catalogue_projection_failure_is_non_fatal(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file
    import artmind.structured.catalogue as catalogue

    def failing_project_catalogue(domain):
        raise RuntimeError("neo4j is down")

    monkeypatch.setattr(catalogue, "project_catalogue", failing_project_catalogue)

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"]])

    result = ingest_structured_file(csv_path, "banking")

    assert result["status"] == "ok"


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


def test_refresh_table_projects_catalogue(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file, refresh_table
    import artmind.structured.catalogue as catalogue

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"]])
    ingest_structured_file(csv_path, "banking")

    spy = {"calls": []}

    def fake_project_catalogue(domain):
        spy["calls"].append(domain)
        return {"tables": 1, "columns": 2, "mappings": 0}

    monkeypatch.setattr(catalogue, "project_catalogue", fake_project_catalogue)

    _write_csv(csv_path, [["id", "name"], [1, "Widget"], [2, "Gadget"]])
    result = refresh_table("products", "banking")

    assert result["status"] == "ok"
    assert spy["calls"] == ["banking"]


def test_ingest_structured_file_same_table_name_two_domains_no_overwrite(tmp_path, monkeypatch):
    """Two domains ingesting a same-named table must both retain their own
    row/data — no silent overwrite of one domain's registration by the other
    (the bug: the registry's old UNIQUE(datasource, table_name) key, with no
    domain, let a later domain's ingest reassign an earlier domain's row)."""
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured import registry
    from artmind.structured.duckdb_adapter import DuckDBDatasource
    from artmind.structured.pipeline import ingest_structured_file

    banking_csv = tmp_path / "banking_products.csv"
    _write_csv(banking_csv, [["id", "name"], [1, "Checking Account"]])
    retail_csv = tmp_path / "retail_products.csv"
    _write_csv(retail_csv, [["id", "name"], [1, "Widget"], [2, "Gadget"]])

    banking_result = ingest_structured_file(banking_csv, "banking", table="products")
    retail_result = ingest_structured_file(retail_csv, "retail", table="products")
    assert banking_result["status"] == "ok"
    assert retail_result["status"] == "ok"

    banking_row = registry.get_table("products", domain="banking")
    retail_row = registry.get_table("products", domain="retail")
    assert banking_row["id"] != retail_row["id"]
    assert banking_row["row_count"] == 1
    assert retail_row["row_count"] == 2

    all_rows = registry.list_tables()
    assert {r["id"] for r in all_rows if r["table_name"] == "products"} == {
        banking_row["id"],
        retail_row["id"],
    }

    # Both domains' views coexist in the shared persistent catalog, reachable
    # by their domain-qualified names since the bare "products" name is
    # ambiguous across the two domains.
    ds = DuckDBDatasource()
    ds.ensure_views(registry.list_tables())
    banking_rows = ds.run_sql("SELECT name FROM banking__products ORDER BY id")
    retail_rows = ds.run_sql("SELECT name FROM retail__products ORDER BY id")
    assert banking_rows == [{"name": "Checking Account"}]
    assert retail_rows == [{"name": "Widget"}, {"name": "Gadget"}]

    import duckdb

    with pytest.raises(duckdb.CatalogException):
        ds.run_sql("SELECT * FROM products")


def test_refresh_table_unregistered_raises(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import refresh_table

    with pytest.raises(ValueError):
        refresh_table("nope", "banking")


# ── ingest_sync CLI dispatch (Task 1.5) ─────────────────────────────────────────


def test_ingest_sync_dispatches_structured_file_not_kg(monkeypatch, tmp_path):
    from click.testing import CliRunner
    import artmind.cli as cli

    csv_path = tmp_path / "products.csv"
    csv.writer(open(csv_path, "w", newline="")).writerows([["id", "name"], [1, "Widget"]])

    spy = {"called": False}

    def fake_ingest_structured(source, domain, **kwargs):
        spy["called"] = True
        spy["source"] = source
        spy["domain"] = domain
        return {"status": "ok", "tables": []}

    monkeypatch.setattr(cli, "ingest_structured_file", fake_ingest_structured)
    monkeypatch.setattr(cli, "ingest_file", lambda *a, **k: pytest.fail("KG path should not run"))
    monkeypatch.setattr(cli, "ingest_to_kg", lambda *a, **k: pytest.fail("KG path should not run"))
    monkeypatch.setattr(cli, "load_env", lambda: {})
    monkeypatch.setattr(cli, "resolve_llm_model", lambda env: "m")

    result = CliRunner().invoke(cli.ingest_sync, [str(csv_path), "--domain", "banking"])
    assert result.exit_code == 0
    assert spy["called"]
    assert spy["domain"] == "banking"


def test_ingest_sync_txt_file_uses_kg_path_not_structured(monkeypatch, tmp_path):
    from click.testing import CliRunner
    import artmind.cli as cli

    f = tmp_path / "a.txt"
    f.write_text("hello")

    monkeypatch.setattr(
        cli, "ingest_structured_file", lambda *a, **k: pytest.fail("structured path should not run")
    )
    monkeypatch.setattr(cli, "ingest_file", lambda *a, **k: {"status": "ok"})
    kg_called = {}
    monkeypatch.setattr(cli, "ingest_to_kg", lambda *a, **k: kg_called.setdefault("yes", True))
    monkeypatch.setattr(cli, "load_env", lambda: {})
    monkeypatch.setattr(cli, "resolve_llm_model", lambda env: "m")

    result = CliRunner().invoke(cli.ingest_sync, [str(f), "--domain", "banking"])
    assert result.exit_code == 0
    assert kg_called.get("yes")


# ── worker dispatch (Task 1.5) ───────────────────────────────────────────────────


def test_process_job_dispatches_structured_source(monkeypatch, tmp_path):
    import artmind.worker as worker

    csv_path = tmp_path / "products.csv"
    csv.writer(open(csv_path, "w", newline="")).writerows([["id", "name"], [1, "Widget"]])

    monkeypatch.setattr(worker, "_get_queued_files", lambda job_id: [str(csv_path)])
    monkeypatch.setattr(worker, "_count_processed", lambda job_id: 0)
    monkeypatch.setattr(worker, "_final_file_statuses", lambda job_id: ["completed"])
    monkeypatch.setattr(worker, "_update_job_status", lambda *a, **k: None)

    file_statuses = []
    monkeypatch.setattr(
        worker,
        "_update_job_file_status",
        lambda job_id, filename, **k: file_statuses.append(k),
    )

    spy = {"called": False}
    monkeypatch.setattr(
        worker,
        "ingest_structured_file",
        lambda *a, **k: (spy.update(called=True), {"status": "ok"})[1],
    )
    monkeypatch.setattr(worker, "ingest_file", lambda *a, **k: pytest.fail("KG path should not run"))
    monkeypatch.setattr(worker, "ingest_to_kg", lambda *a, **k: pytest.fail("KG path should not run"))

    worker._process_job("job-1", "banking", {})

    assert spy["called"]
    assert any(s.get("status") == "completed" for s in file_statuses)
