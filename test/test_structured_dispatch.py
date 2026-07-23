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


def test_ingest_structured_file_attempts_mapping_proposal(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file
    import artmind.structured.mappings as mappings

    spy = {"called": False}

    def fake_propose_mappings(table_id, domains, **kwargs):
        spy["called"] = True
        spy["table_id"] = table_id
        spy["domains"] = domains
        return []

    monkeypatch.setattr(mappings, "propose_mappings", fake_propose_mappings)

    csv_path = tmp_path / "products.csv"
    _write_csv(csv_path, [["id", "name"], [1, "Widget"], [2, "Gadget"]])

    result = ingest_structured_file(csv_path, "banking")

    assert result["status"] == "ok"
    assert spy["called"]
    assert spy["domains"] == ["banking"]
    assert spy["table_id"] == result["tables"][0]["table_id"]


def test_ingest_structured_file_mapping_proposal_failure_is_non_fatal(tmp_path, monkeypatch):
    _patch_stores(tmp_path, monkeypatch)
    from artmind.structured.pipeline import ingest_structured_file
    import artmind.structured.mappings as mappings

    def failing_propose_mappings(table_id, domains, **kwargs):
        raise RuntimeError("graph is down")

    monkeypatch.setattr(mappings, "propose_mappings", failing_propose_mappings)

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
