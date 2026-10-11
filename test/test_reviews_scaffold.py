"""Building a dossier from a recorded trace."""

import json
import time

import pytest

from artmind.reviews import dossier as dz
from artmind.reviews import index, scaffold
from artmind.reviews.validate import validate
from artmind.traces import store


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")


def _trace(tid="t1"):
    store.insert_turn(tid, source="admin", question="How many trips did Person A take to City B?",
                      started_at=time.time())
    store.update_turn(tid, answer="Person A went twice.\n\n### Sources\n- [x](<j/2025-01-01.md>) — d\n",
                      status="done", turns=7, cost_usd=0.2, fell_through=1, answer_basis="vector",
                      graph_attempts=2, graph_productive=0, domains='["d"]',
                      graph_state=json.dumps({"artmind_version": "9.9", "schemas": {"d": "abc"}, "vault_head": "f00"}),
                      retrieval_path=json.dumps([
                          {"span_id": "s1", "layer": "graph", "command": "query graph pattern6", "rows": 0, "productive": False},
                          {"span_id": "s2", "layer": "vector", "command": "query vector-text", "rows": 4, "productive": True}]))
    store.insert_span({"span_id": "s0", "trace_id": tid, "command": "query entity-resolve",
                       "args": json.dumps({"domain": ["d"], "reference": "Person A", "compact": True, "top_k": 5}),
                       "exit_code": 0, "duration_ms": 1, "rows": 2, "rows_total": None, "truncated": 0,
                       "evidence": json.dumps({"resolved": [{"id": "e1", "name": "Person A", "score": 0.03},
                                                            {"id": "e2", "name": "Person Ab", "score": 0.02}]}),
                       "started_at": time.time()}, [])
    store.insert_span({"span_id": "s3", "trace_id": tid, "command": "query graph entity-listing",
                       "args": json.dumps({"domain": ["d"], "name_filter": "City B",
                                           "entity_id": "448613e83d8d2476fbdd2b62d2db00fd"}),
                       "exit_code": 0, "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0,
                       "evidence": "{}", "started_at": time.time()}, [])
    store.add_feedback(tid, "wrong", "Three trips, not two")


def test_harvest_private_terms():
    _trace()
    from artmind.traces.analysis import show

    terms = scaffold.harvest_private_terms(show("t1"))
    assert terms == ["Person A", "Person Ab", "City B"]


def test_scaffold_prefills_from_the_trace():
    _trace()
    d = scaffold.scaffold("t1", stage="schema", elements=["d/Person-WENT_TO->"])
    assert d.meta["status"] == "open"
    assert d.meta["stage"] == "schema"
    assert d.meta["trace_ids"] == ["t1"]
    assert d.meta["graph_state"]["vault_head"] == "f00"
    assert "Person A" in d.meta["private_terms"]
    symptom = d.sections["Symptom"]
    assert "> How many trips did Person A take to City B?" in symptom
    assert "**Rating:** wrong — expected: Three trips, not two" in symptom
    assert "| 1 | graph | `query graph pattern6` | 0 |" in symptom
    assert "fell through: yes" in symptom and "`vector`" in symptom
    assert d.sections["Agent prompt"].startswith(dz.PRIVACY_PREAMBLE)
    assert "**Question:** How many trips did Person A take to City B?" in d.sections["Regression question"]
    assert d.sections["Log"].endswith("opened from trace `t1`")
    result = validate(d)  # placeholders are not content
    assert not result["ok"] and any("Diagnosis" in e for e in result["errors"])


def test_scaffold_unknown_trace():
    with pytest.raises(dz.DossierError):
        scaffold.scaffold("missing")


def test_index_and_open_reviews_for():
    _trace()
    d = scaffold.scaffold("t1")
    path = dz.save(d)
    index.index_dossier(d, path)
    assert scaffold.open_reviews_for("t1") == [d.review_id]
    d.meta["status"] = "dismissed"
    dz.save(d)
    assert scaffold.open_reviews_for("t1") == []
    assert store.list_turns()[0]["reviewed"] == 1


def test_reindex_rebuilds_from_files():
    _trace()
    d = scaffold.scaffold("t1")
    dz.save(d)
    (dz.reviews_dir() / "junk.md").write_text("garbage")
    store.index_review("rv-2000-01-01-aaaaaa", ["ghost"], stage=None, elements=[], status="open", dossier_path=None)
    result = index.reindex()
    assert result["indexed"] == 1 and result["links"] == 1
    assert [i["path"].endswith("junk.md") for i in result["invalid"]] == [True]
    assert store.clear_review_index() == 1  # only the real dossier's row survived the rebuild


def test_ids_are_not_private_terms():
    shown = {"events": [], "unattributed_spans": [{"evidence": {}, "args": {
        "id_list": ["01a0fd39-a36f-72d0-9ff0-1c3e39a74976_001"], "entity_id1": "abc", "chunk_ids": ["x" * 20],
        "reference": "Person A", "name_filter": "01a0fd39-a36f-72d0-9ff0-1c3e39a74976_002"}}]}
    assert scaffold.harvest_private_terms(shown) == ["Person A"]
