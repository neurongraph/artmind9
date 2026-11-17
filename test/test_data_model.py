"""The data-model gate (spec 2026-11-15 §8.1)."""
import pytest
import yaml

from artmind import data_model as dm


@pytest.fixture
def model(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "MODEL_FILE", tmp_path / "data_model.yaml")
    monkeypatch.setattr(dm, "DB_PATH", tmp_path / "document_registry.db")
    monkeypatch.setattr(dm, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(dm, "_warned", False)
    return tmp_path


def test_a_fresh_vault_starts_current_and_writes_the_file(model):
    assert dm.recorded() == dm.CURRENT
    assert yaml.safe_load((model / "data_model.yaml").read_text())["version"] == dm.CURRENT


def test_an_existing_vault_without_the_file_is_at_zero(model):
    (model / "document_registry.db").write_bytes(b"")
    assert dm.recorded() == 0
    assert not (model / "data_model.yaml").exists()


def test_require_current_names_the_command_and_migrate_status(model):
    (model / "document_registry.db").write_bytes(b"")
    with pytest.raises(dm.DataModelBehind, match=r"ingest sync: .*model 0.*artmind migrate status"):
        dm.require_current("ingest sync")


def test_stamp_then_current_passes(model):
    (model / "document_registry.db").write_bytes(b"")
    dm.stamp(dm.CURRENT)
    dm.require_current("ingest sync")


def test_warn_is_once_per_process(model, capsys):
    (model / "document_registry.db").write_bytes(b"")
    dm.warn_if_behind()
    dm.warn_if_behind()
    assert capsys.readouterr().err.count("artmind migrate status") == 1


def test_warn_never_writes_the_file(model):
    dm.warn_if_behind()
    assert not (model / "data_model.yaml").exists()


def test_a_malformed_file_is_behind(model):
    (model / "data_model.yaml").write_text("version: [not a number]\n")
    assert dm.recorded() == 0


def test_the_worker_fails_a_job_on_a_vault_that_is_behind(model, monkeypatch):
    from artmind import worker

    (model / "document_registry.db").write_bytes(b"")
    seen = {}
    monkeypatch.setattr(worker, "_update_job_status", lambda job_id, **kw: seen.update(kw))
    monkeypatch.setattr(worker, "_get_queued_files", lambda j: pytest.fail("the job must not start"))
    worker._process_job("job-1", "general", {})
    assert seen["status"] == "failed"
    assert "artmind migrate status" in seen["error_message"]


def test_a_curate_write_route_answers_409_with_the_message(model):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from artmind.webui import curate_routes

    (model / "document_registry.db").write_bytes(b"")
    app = FastAPI()
    curate_routes.register_curate_routes(app)
    client = TestClient(app)
    resp = client.post("/api/curate/sweep", json={"dry_run": True})
    assert resp.status_code == 409
    assert "artmind migrate status" in resp.json()["detail"]
