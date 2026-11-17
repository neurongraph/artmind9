"""The data-model gate (spec 2026-11-15 §8.1)."""
import pytest
import yaml

from artmind import data_model as dm


@pytest.fixture
def model(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "MODEL_FILE", tmp_path / "data_model.yaml")
    monkeypatch.setattr(dm, "DB_PATH", tmp_path / "document_registry.db")
    monkeypatch.setattr(dm, "KG_DIR", tmp_path / "kg")
    monkeypatch.setattr(dm, "CURATION_DIR", tmp_path / "curation")
    monkeypatch.setattr(dm, "SAME_AS_FILE", tmp_path / "same_as.yaml")
    monkeypatch.setattr(dm, "_warned", False)
    monkeypatch.setattr(dm, "_graph_cache", {})
    monkeypatch.setattr(dm, "read_graph_version", lambda stamp_fresh=False: dm.CURRENT)
    return tmp_path


def _db(model):
    (model / "document_registry.db").write_bytes(b"")


def _file_version(model):
    return yaml.safe_load((model / "data_model.yaml").read_text())["version"]


def test_a_fresh_vault_is_current_and_a_read_writes_nothing(model, capsys):
    assert dm.recorded() == dm.CURRENT
    dm.warn_if_behind()
    assert not (model / "data_model.yaml").exists()
    assert capsys.readouterr().err == ""


def test_the_first_write_on_an_empty_vault_stamps_it(model):
    dm.require_current("ingest sync")
    assert _file_version(model) == dm.CURRENT


def test_a_dry_run_stamps_nothing(model):
    import click

    with click.Context(click.Command("x"), obj=None) as ctx:
        ctx.params["dry_run"] = True
        dm.require_current("ingest sync")
    assert not (model / "data_model.yaml").exists()


def test_an_existing_vault_without_the_file_is_at_zero(model):
    _db(model)
    assert dm.recorded() == 0
    assert not (model / "data_model.yaml").exists()


def test_a_clone_has_staging_but_no_registry_and_is_behind(model):
    (model / "kg" / "journal" / "note-one").mkdir(parents=True)
    assert dm.recorded() == 0
    with pytest.raises(dm.DataModelBehind, match="model 0"):
        dm.require_current("ingest sync")
    assert not (model / "data_model.yaml").exists()


def test_a_clone_with_same_as_is_behind(model):
    (model / "same_as.yaml").write_text("groups: []\n")
    assert dm.recorded() == 0


def test_a_clone_with_curation_records_is_behind(model):
    (model / "curation").mkdir()
    (model / "curation" / "r.json").write_text("{}")
    assert dm.recorded() == 0


def test_empty_kg_and_curation_folders_are_still_fresh(model):
    (model / "kg").mkdir()
    (model / "curation").mkdir()
    assert dm.recorded() == dm.CURRENT


def test_require_current_names_the_command_and_migrate_status(model):
    _db(model)
    with pytest.raises(dm.DataModelBehind, match=r"ingest sync: .*model 0.*artmind migrate status"):
        dm.require_current("ingest sync")


def test_stamp_then_current_passes(model):
    _db(model)
    dm.stamp(dm.CURRENT)
    dm.require_current("ingest sync")
    assert list(model.glob(".data_model.*")) == []


def test_warn_is_once_per_process(model, capsys):
    _db(model)
    dm.warn_if_behind()
    dm.warn_if_behind()
    assert capsys.readouterr().err.count("artmind migrate status") == 1


def test_a_malformed_file_has_its_own_message(model):
    (model / "data_model.yaml").write_text("version: [not a number]\n")
    assert dm.recorded() == 0
    with pytest.raises(dm.DataModelBehind, match="unreadable"):
        dm.require_current("ingest sync")


def test_a_newer_file_says_upgrade(model):
    dm.stamp(dm.CURRENT + 1)
    with pytest.raises(dm.DataModelBehind, match="Upgrade artmind"):
        dm.require_current("ingest sync")


def test_a_read_carries_the_warning_into_the_json_envelope(model):
    import click

    _db(model)
    with click.Context(click.Command("x")) as ctx:
        dm.warn_if_behind()
        assert "artmind migrate status" in ctx.find_root().meta[dm.WARNING_META]


# ── the graph's marker ───────────────────────────────────────────────────────


class _Rows:
    def __init__(self, row):
        self.row = row

    def single(self):
        return self.row

    def consume(self):
        return None


class _FakeSession:
    def __init__(self, marker=None, populated=False):
        self.marker, self.populated, self.sent = marker, populated, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def run(self, query, **params):
        self.sent.append((query, params))
        if "RETURN m.version" in query:
            return _Rows(None if self.marker is None else {"version": self.marker})
        if "populated" in query:
            return _Rows({"populated": self.populated})
        return _Rows(None)


_REAL_READ = dm.read_graph_version  # captured before any fixture stubs it


def _use_graph(monkeypatch, session):
    monkeypatch.setattr(dm, "read_graph_version", _REAL_READ)
    monkeypatch.setattr(dm, "_open_session", lambda: session)


_MARK = "MERGE (m:DataModel {id: 'artmind'}) SET m.version = $version"


def test_file_current_graph_behind_is_refused(model, monkeypatch):
    sess = _FakeSession(populated=True)
    _use_graph(monkeypatch, sess)
    dm.stamp(dm.CURRENT)
    with pytest.raises(dm.DataModelBehind, match=r"this graph's data is at model 0.*artmind migrate status"):
        dm.require_current("ingest sync")
    assert not any("MERGE" in q for q, _ in sess.sent)


def test_a_fresh_graph_is_marked_current_by_the_first_write(model, monkeypatch):
    sess = _FakeSession(populated=False)
    _use_graph(monkeypatch, sess)
    dm.stamp(dm.CURRENT)
    dm.require_current("ingest sync")
    assert [(q, p) for q, p in sess.sent if "MERGE" in q] == [(_MARK, {"version": dm.CURRENT})]


def test_a_dry_run_does_not_mark_the_graph(model, monkeypatch):
    import click

    sess = _FakeSession(populated=False)
    _use_graph(monkeypatch, sess)
    dm.stamp(dm.CURRENT)
    with click.Context(click.Command("x")) as ctx:
        ctx.params["dry_run"] = True
        dm.require_current("ingest sync")
    assert not any("MERGE" in q for q, _ in sess.sent)


def test_a_marked_graph_at_current_passes_and_is_cached(model, monkeypatch):
    sess = _FakeSession(marker=dm.CURRENT)
    _use_graph(monkeypatch, sess)
    dm.stamp(dm.CURRENT)
    dm.require_current("ingest sync")
    dm.require_current("ingest sync")
    assert len(sess.sent) == 1  # one read per process


def test_an_unreachable_graph_does_not_block_a_write(model, monkeypatch):
    def boom():
        raise OSError("no route")

    monkeypatch.setattr(dm, "read_graph_version", _REAL_READ)
    monkeypatch.setattr(dm, "_open_session", boom)
    dm.stamp(dm.CURRENT)
    dm.require_current("views save")


def test_a_read_warns_when_only_the_graph_is_behind(model, monkeypatch, capsys):
    _use_graph(monkeypatch, _FakeSession(populated=True))
    dm.stamp(dm.CURRENT)
    dm.warn_if_behind()
    assert "this graph's data is at model 0" in capsys.readouterr().err


def test_stamp_graph_sends_the_marker_cypher(model):
    sess = _FakeSession()
    dm.stamp_graph(sess, 1)
    assert sess.sent == [(_MARK, {"version": 1})]


def test_stamp_if_fresh_follows_the_same_rule(tmp_path):
    fresh, clone = tmp_path / "a" / "m.yaml", tmp_path / "b" / "m.yaml"
    dm.stamp_if_fresh(fresh, tmp_path / "a.db", kg_dir=tmp_path / "a" / "kg",
                      same_as=tmp_path / "a" / "s.yaml", curation_dir=tmp_path / "a" / "c")
    assert yaml.safe_load(fresh.read_text())["version"] == dm.CURRENT
    (tmp_path / "b" / "kg" / "d" / "doc").mkdir(parents=True)
    dm.stamp_if_fresh(clone, tmp_path / "b.db", kg_dir=tmp_path / "b" / "kg",
                      same_as=tmp_path / "b" / "s.yaml", curation_dir=tmp_path / "b" / "c")
    assert not clone.exists()


def test_the_worker_fails_a_job_on_a_vault_that_is_behind(model, monkeypatch):
    from artmind import worker

    _db(model)
    seen = {}
    monkeypatch.setattr(worker, "_update_job_status", lambda job_id, **kw: seen.update(kw))
    monkeypatch.setattr(worker, "_get_queued_files", lambda j: pytest.fail("the job must not start"))
    worker._process_job("job-1", "general", {})
    assert seen["status"] == "failed"
    assert "artmind migrate status" in seen["error_message"]


@pytest.mark.parametrize("method,path", [
    ("post", "/api/curate/sweep"),
])
def test_a_curate_write_route_answers_409_with_the_message(model, method, path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from artmind.webui import curate_routes

    _db(model)
    app = FastAPI()
    curate_routes.register_curate_routes(app)
    resp = TestClient(app).post(path, json={"dry_run": True})
    assert resp.status_code == 409
    assert "artmind migrate status" in resp.json()["detail"]


def test_the_other_web_write_routes_are_gated():
    from fastapi import FastAPI

    from artmind.webui import dashboard_routes, insights_routes
    from artmind.webui.data_model_gate import data_model_gate

    app = FastAPI()
    dashboard_routes.register_dashboard_routes(app, None)
    insights_routes.register_insights_routes(app)
    gated = {r.path for r in app.routes if hasattr(r, "dependant") and any(d.call is data_model_gate for d in r.dependant.dependencies)}
    gated = {p for p in gated}
    for path in ("/api/ingest", "/api/artifacts/pull", "/api/snapshots/{name}/restore", "/api/embed-entities",
                 "/api/structured/ingest", "/api/insights/same-as/propose",
                 "/api/artifacts/{domain}/{doc}/write-to-graph"):
        assert path in gated, path


def test_db_mappings_accept_proposed_is_gated(model):
    from click.testing import CliRunner

    from artmind.cli import cli

    _db(model)
    result = CliRunner().invoke(cli, ["db", "mappings", "some_table", "--acceptProposed"])
    assert result.exit_code == 1
    assert "db mappings --acceptProposed" in result.output
    assert "artmind migrate status" in result.output


def test_a_group_callback_does_not_warn_when_a_subcommand_runs(model, capsys):
    from click.testing import CliRunner

    from artmind.cli import cli

    _db(model)
    CliRunner().invoke(cli, ["db", "mappings", "some_table", "clear", "--column", "c", "--dryRun"])
    assert capsys.readouterr().err.count("artmind migrate status") <= 1
