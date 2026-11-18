"""`stamp_if_done`: both markers are written only once no step is pending."""
from contextlib import contextmanager

import pytest

from artmind import data_model as dm
from artmind.migrations.temporal_v1 import steps as steps_mod
from artmind.migrations.temporal_v1.steps import MigrationContext, Step

_REAL_READ = dm.read_graph_version  # captured before the autouse conftest stub
MARK = "MERGE (m:DataModel {id: 'artmind'}) SET m.version = $version"


class Session:
    def __init__(self, behind=True):
        self.sent, self.behind = [], behind

    def run(self, query, **params):
        self.sent.append((query, params))
        outer = self

        class R:
            def consume(self):
                return None

            def single(self):
                if "RETURN m.version" in query:
                    return {"version": None}
                return {"populated": outer.behind}
        return R()


@pytest.fixture
def model(tmp_path, monkeypatch):
    monkeypatch.setattr(dm, "MODEL_FILE", tmp_path / "data_model.yaml")
    monkeypatch.setattr(dm, "DB_PATH", tmp_path / "document_registry.db")
    monkeypatch.setattr(dm, "KG_DIR", tmp_path / "kg")
    monkeypatch.setattr(dm, "CURATION_DIR", tmp_path / "curation")
    monkeypatch.setattr(dm, "SAME_AS_FILE", tmp_path / "same_as.yaml")
    monkeypatch.setattr(dm, "_graph_cache", {})
    monkeypatch.setattr(dm, "read_graph_version", _REAL_READ)
    session = Session()
    monkeypatch.setattr(dm, "_open_session", lambda: _cm(session))
    return session


@contextmanager
def _cm(session):
    yield session


def _ctx(session):
    return MigrationContext(domains=None, session_factory=lambda: _cm(session))


def _steps(monkeypatch, **left):
    monkeypatch.setattr(steps_mod, "STEPS", [
        Step(name, lambda ctx, n=n: ({"n": n, "by_domain": {"d1": n}} if n else {"by_domain": {"d1": 0}}),
             lambda ctx, dry: {}) for name, n in left.items()])


def test_a_pending_step_stamps_neither_marker(model, monkeypatch):
    _steps(monkeypatch, one=0, two=3)
    out = steps_mod.stamp_if_done(_ctx(model))
    assert out == {"pending": [{"step": "two", "domains": ["d1"]}]}
    assert not dm.MODEL_FILE.exists() and not any("MERGE" in q for q, _ in model.sent)


def test_info_keys_never_block_and_both_markers_are_stamped(model, monkeypatch):
    _steps(monkeypatch, one=0, two=0)
    assert steps_mod.stamp_if_done(_ctx(model)) == {"version": 1}
    assert "version: 1" in dm.MODEL_FILE.read_text()
    assert [(q, p) for q, p in model.sent if "MERGE" in q] == [(MARK, {"version": 1})]
    model.sent.clear()
    dm.require_current("ingest sync")  # same process: the cache says current, no re-read
    assert model.sent == []


def test_a_second_machine_stamps_its_graph_when_the_file_was_pulled_current(model, monkeypatch):
    dm.stamp(1)  # pulled with git
    _steps(monkeypatch, one=0)
    with pytest.raises(dm.DataModelBehind):  # its graph is still behind
        dm.require_current("ingest sync")
    assert steps_mod.stamp_if_done(_ctx(model)) == {"version": 1}
    assert any(q == MARK for q, _ in model.sent)
    dm.require_current("ingest sync")


def test_a_second_machine_with_graph_steps_pending_stamps_nothing_more(model, monkeypatch):
    dm.stamp(1)
    _steps(monkeypatch, documents_to_commit=2)
    assert "pending" in steps_mod.stamp_if_done(_ctx(model))
    assert not any(q == MARK for q, _ in model.sent)


def test_an_unreadable_step_is_not_done(model, monkeypatch):
    def boom(ctx):
        raise RuntimeError("no graph")
    monkeypatch.setattr(steps_mod, "STEPS", [Step("s", boom, lambda c, d: {})])
    out = steps_mod.stamp_if_done(_ctx(model))
    assert out["pending"][0]["step"] == "s" and "no graph" in out["pending"][0]["error"]
    assert not dm.MODEL_FILE.exists()
