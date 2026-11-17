"""`_retired_at` / `_retired_by` on every relabel to History (spec 2026-11-15 §6.1, plan question 8)."""
import json

import pytest
from _neo4j_recorder import Recorder

from artmind import ingest, lifecycle, passage_store, projection, summary_store


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id: set())


def _history_writes(rec):
    return [(c, p) for c, p in rec.calls if "History" in c and "_retired_at = $now" in c]


def test_version_supersession_stamps_every_label():
    rec = Recorder(lambda c, p: [{"n": 1}])
    ingest._retract_prior_version(rec, "pj", "d1", retired_by="version:3")
    writes = _history_writes(rec)
    assert len(writes) == 4
    for cypher, params in writes:
        assert "_retired_at = $now" in cypher and "_retired_by = $by" in cypher
        assert params["by"] == "version:3" and params["now"].endswith("+00:00")


def test_the_commit_retires_the_prior_version_with_its_own_timestamp(tmp_path):
    folder = tmp_path / "doc"
    folder.mkdir()
    (folder / "document.json").write_text(json.dumps({"id": "d1", "name": "a.md", "version": 4}))
    for name in ("chunks.json", "observations.json", "relationships.json"):
        (folder / name).write_text("[]")
    rec = Recorder(lambda c, p: [])
    ingest._commit_document_tx(rec, ingest._load_staged(folder, "pj"), True)
    writes = _history_writes(rec)
    assert writes and {p["by"] for _, p in writes} == {"version:4"}
    recorded = {p["recorded_at"] for _, p in rec.calls if "recorded_at" in p}
    assert {p["now"] for _, p in writes} == recorded


def test_a_vanished_source_is_retired_as_removed(monkeypatch):
    from contextlib import contextmanager

    rec = Recorder(lambda c, p: [{"n": 1}])

    class _Session(Recorder):
        def execute_write(self, fn):
            return fn(rec)

    @contextmanager
    def _fake():
        yield _Session()

    monkeypatch.setattr("artmind.graph_query.neo4j_session", _fake)
    ingest.retract_document("d1", "pj")
    writes = _history_writes(rec)
    assert len(writes) == 6, "observations, chunks, passages, summaries, Document, UserChat"
    assert {p["by"] for _, p in writes} == {"removed"}
    assert len({p["now"] for _, p in writes}) == 1


def test_passage_and_summary_retires_name_their_cause():
    rec = Recorder()
    passage_store.write_chunk_passages(rec, "c1", [], recorded_at="2026-11-16T00:00:00+00:00", retired_by="resplit")
    cypher, params = rec.calls[0]
    assert "SET p:DocPassageHistory, p._retired_at = $now, p._retired_by = $by" in cypher
    assert params == {"chunk_id": "c1", "now": "2026-11-16T00:00:00+00:00", "by": "resplit"}
    rec = Recorder()
    summary_store.write_document_summaries(rec, "d1", [], recorded_at="2026-11-16T00:00:00+00:00",
                                           retired_by="version:2")
    cypher, params = rec.calls[0]
    assert "SET s:DocSummaryHistory, s._retired_at = $now, s._retired_by = $by" in cypher
    assert params == {"doc_id": "d1", "now": "2026-11-16T00:00:00+00:00", "by": "version:2"}


def test_retire_and_restore():
    rec = Recorder(lambda c, p: [{"n": 1}])
    lifecycle._transition(rec, "d1", to_history=True, rebuild=False, retired_by="supersede:abc")
    writes = [(c, p) for c, p in rec.calls if "History" in c and "REMOVE" in c]
    assert len(writes) == 5, "observations, document, chunks, passages, summaries"
    for cypher, params in writes:
        assert "_retired_at = $now" in cypher and "_retired_by = $by" in cypher
        assert params["by"] == "supersede:abc" and params["now"].endswith("+00:00")
    rec = Recorder(lambda c, p: [{"n": 1}])
    lifecycle._transition(rec, "d1", to_history=False, rebuild=False)
    moves = [(c, p) for c, p in rec.calls if "History" in c and "REMOVE" in c]
    assert len(moves) == 5
    for cypher, params in moves:
        assert "_revived_at = $now" in cypher and "REMOVE" in cypher and "._retired_at" in cypher
        assert "._retired_by" in cypher and "by" not in params


def test_retire_by_default_says_retire():
    rec = Recorder(lambda c, p: [{"n": 1}])
    lifecycle._transition(rec, "d1", to_history=True, rebuild=False)
    assert {p["by"] for c, p in rec.calls if "History" in c and "REMOVE" in c} == {"retire"}


def test_a_retraction_names_its_retractor():
    rec = Recorder(lambda c, p: [{"key": "a|PERSON|pj"}])
    projection.apply_retractions(rec, [{"id": "o9", "_retracts": "o1"}], retired_at="2026-11-16T00:00:00+00:00")
    ((cypher, params),) = rec.queries_containing("SET o:ObservationHistory")
    assert "o._retired_by = 'retract:' + $retractor" in cypher and "o._retired_at = $now" in cypher
    assert params == {"id": "o1", "retractor": "o9", "now": "2026-11-16T00:00:00+00:00"}


def test_apply_supersession_retires_by_its_record(monkeypatch, tmp_path):
    from contextlib import contextmanager

    import paths
    from artmind import supersession_records, temporal

    monkeypatch.setattr(paths, "CURATION_DIR", tmp_path / "curation")

    @contextmanager
    def _fake(*a, **k):
        yield Recorder()

    seen = {}
    monkeypatch.setattr(temporal, "neo4j_session", _fake)
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda doc_id, *a, **k: seen.update(k))
    temporal.apply_supersession("new-doc", "old-doc", "document", "2026-03-01")
    rid = supersession_records.record_id("new-doc", "old-doc", "document")
    assert seen["retired_by"] == f"supersede:{rid}"
