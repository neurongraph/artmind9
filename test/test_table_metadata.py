"""Column descriptions for text2sql: the library, `db metadata describe`, and the Curate route."""
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import paths
from artmind import table_metadata
from artmind.webui import curate_routes

VIEW = "bank__complaints"


@pytest.fixture
def meta_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DOMAIN_METADATA_DIR", tmp_path)
    return tmp_path


def _file(meta_dir):
    return yaml.safe_load((meta_dir / "bank.meta.yaml").read_text())


def test_describe_a_column_creates_the_file(meta_dir):
    r = table_metadata.describe("bank", "complaints", "  hours booked, may be negative ", "HRS")
    assert r["changed"] and r["after"] == "hours booked, may be negative"
    assert _file(meta_dir)["tables"][VIEW]["columns"]["HRS"] == {"description": "hours booked, may be negative"}
    assert table_metadata.read("bank", "complaints")["columns"] == {"HRS": "hours booked, may be negative"}


def test_other_keys_survive_a_save(meta_dir):
    (meta_dir / "bank.meta.yaml").write_text(yaml.safe_dump({
        "version": 1, "database": {"rules": ["no test rows"]},
        "tables": {VIEW: {"grain": "one row per complaint", "columns": {"HRS": {"values": {"A": "alpha"}}}}}}))
    table_metadata.describe("bank", "complaints", "weekly hours", "HRS")
    table_metadata.describe("bank", "complaints", "complaints register")
    data = _file(meta_dir)
    assert data["database"]["rules"] == ["no test rows"]
    assert data["tables"][VIEW]["grain"] == "one row per complaint"
    assert data["tables"][VIEW]["columns"]["HRS"] == {"values": {"A": "alpha"}, "description": "weekly hours"}
    assert data["tables"][VIEW]["description"] == "complaints register"


def test_dry_run_and_no_change_write_nothing(meta_dir):
    table_metadata.describe("bank", "complaints", "x", "HRS", dry_run=True)
    assert not (meta_dir / "bank.meta.yaml").exists()
    table_metadata.describe("bank", "complaints", "x", "HRS")
    before = (meta_dir / "bank.meta.yaml").stat().st_mtime_ns
    assert table_metadata.describe("bank", "complaints", "x", "HRS")["changed"] is False
    assert (meta_dir / "bank.meta.yaml").stat().st_mtime_ns == before


def test_empty_text_removes_the_description(meta_dir):
    table_metadata.describe("bank", "complaints", "x", "HRS")
    table_metadata.describe("bank", "complaints", "", "HRS")
    assert table_metadata.read("bank", "complaints")["columns"] == {}


def test_the_merged_sidecar_carries_the_description(meta_dir, monkeypatch):
    from artmind import sql_browser

    monkeypatch.setattr(paths, "LOGS_DIR", meta_dir / "logs")
    table_metadata.describe("bank", "complaints", "weekly hours", "HRS")
    merged = yaml.safe_load(sql_browser.metadata_for(["bank"]).read_text())
    assert merged["tables"][VIEW]["columns"]["HRS"]["description"] == "weekly hours"


@pytest.fixture
def client():
    app = FastAPI()
    curate_routes.register_curate_routes(app)
    return TestClient(app)


def test_route_passes_parameters_and_previews(client, monkeypatch):
    seen = {}
    monkeypatch.setattr(curate_routes, "_describe",
                        lambda d, t, c, text, dry: seen.update(a=(d, t, c, text, dry)) or
                        {"path": "p", "changed": True, "before": "", "after": text})
    r = client.post("/api/curate/tables/complaints/describe",
                    json={"domain": "bank", "column": "HRS", "text": "weekly hours"})
    assert r.status_code == 200 and seen["a"] == ("bank", "complaints", "HRS", "weekly hours", True)
    assert r.json()["dry_run"] is True and "HRS" in r.json()["effect"]["summary"]
    client.post("/api/curate/tables/complaints/describe",
                json={"domain": "bank", "text": "t", "dry_run": False})
    assert seen["a"] == ("bank", "complaints", None, "t", False)


# ── batch route behind the multi-select table ─────────────────────────────

OPS = [{"kind": "mapping", "action": "confirm", "column": "A", "entity_class": "METRIC"},
       {"kind": "mapping", "action": "clear", "column": "B"},
       {"kind": "bridge", "action": "confirm", "column": "C"},
       {"kind": "describe", "column": "D", "text": "weekly hours"}]


def test_batch_dry_run_summarises_and_writes_nothing(client, monkeypatch):
    sent = []
    monkeypatch.setattr(curate_routes, "_table_write", lambda *a: sent.append(a))
    monkeypatch.setattr(curate_routes, "_describe", lambda *a: sent.append(a))
    r = client.post("/api/curate/tables/t/batch", json={"domain": "d", "ops": OPS})
    assert r.status_code == 200 and r.json()["dry_run"] is True and sent == []
    summary = r.json()["effect"]["summary"]
    assert "confirm mapping x1" in summary and "clear mapping x1" in summary and "describe x1" in summary


def test_batch_real_run_sends_every_op_with_its_parameters(client, monkeypatch):
    writes, descs = [], []
    monkeypatch.setattr(curate_routes, "_table_write", lambda *a: writes.append(a))
    monkeypatch.setattr(curate_routes, "_describe", lambda *a: descs.append(a))
    r = client.post("/api/curate/tables/t/batch", json={"domain": "d", "ops": OPS, "dry_run": False})
    assert r.status_code == 200 and [x["ok"] for x in r.json()["results"]] == [True] * 4
    assert writes == [("mapping", "t", "d", "confirm", "A", "METRIC"),
                      ("mapping", "t", "d", "clear", "B", None),
                      ("bridge", "t", "d", "confirm", "C", None)]
    assert descs == [("d", "t", "D", "weekly hours", False)]


def test_batch_reports_a_failing_op_but_still_runs_the_rest(client, monkeypatch):
    done = []

    def write(kind, table, domain, action, column, value):
        if column == "A":
            raise ValueError("no mapping for column 'A'")
        done.append(column)

    monkeypatch.setattr(curate_routes, "_table_write", write)
    monkeypatch.setattr(curate_routes, "_describe", lambda *a: done.append(a[2]))
    r = client.post("/api/curate/tables/t/batch", json={"domain": "d", "ops": OPS, "dry_run": False})
    assert r.status_code == 400 and "3 done, 1 failed" in r.json()["detail"] and "no mapping" in r.json()["detail"]
    assert done == ["B", "C", "D"]


@pytest.mark.parametrize("op", [
    {"kind": "mapping", "action": "set", "column": "A"},
    {"kind": "mapping", "action": "explode", "column": "A", "entity_class": "X"},
    {"kind": "bridge", "action": "set", "column": "A"},
    {"kind": "nonsense"},
])
def test_batch_rejects_a_bad_op_before_writing_anything(client, monkeypatch, op):
    monkeypatch.setattr(curate_routes, "_table_write", lambda *a: pytest.fail("wrote"))
    r = client.post("/api/curate/tables/t/batch", json={"domain": "d", "ops": [OPS[0], op], "dry_run": False})
    assert r.status_code == 400


def test_batch_needs_at_least_one_op(client):
    assert client.post("/api/curate/tables/t/batch", json={"domain": "d", "ops": []}).status_code == 400
