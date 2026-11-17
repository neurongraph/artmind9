"""artmind.lifecycle — resolve_document_id must reach a document in history too.

Found live in Phase 5: restore-from-archive deliberately leaves a document in
`history`, and `resolve_document_id`'s Cypher matched `(d:Document)` only, so
`docs restore --documentName <anything, even the exact id>` could not find it.
Fixed in Phase 6 (see docs/redesign-phase5-implementation-notes.md, open
question 4).
"""
from unittest.mock import MagicMock, patch

from artmind.lifecycle import resolve_document_id


def test_resolve_document_id_matches_both_labels():
    session = MagicMock()
    session.run.return_value.data.return_value = [{"id": "doc-1", "name": "Some Doc"}]

    with patch("artmind.graph_query.read_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        result = resolve_document_id("Some Doc")

    assert result == "doc-1"
    cypher = session.run.call_args[0][0]
    assert "MATCH (d) WHERE (d:Document OR d:DocumentHistory)" in cypher


def test_resolve_document_id_finds_a_document_in_history():
    """The exact Phase 5 gap: a document restore-from-archive just retired."""
    session = MagicMock()
    session.run.return_value.data.return_value = [{"id": "hist-doc-1", "name": "Restored Doc"}]

    with patch("artmind.graph_query.read_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        result = resolve_document_id("hist-doc-1")

    assert result == "hist-doc-1"


def test_resolve_document_id_returns_none_when_nothing_matches():
    session = MagicMock()
    session.run.return_value.data.return_value = []

    with patch("artmind.graph_query.read_session") as mock_ctx:
        mock_ctx.return_value.__enter__.return_value = session
        assert resolve_document_id("nope") is None


def test_transition_swaps_passages_both_ways():
    from _neo4j_recorder import Recorder
    from artmind import lifecycle, projection

    for to_history, frm, to in ((True, "DocPassage", "DocPassageHistory"), (False, "DocPassageHistory", "DocPassage")):
        tx = Recorder(lambda c, p: [{"n": 0}])
        orig = projection.keys_for_document
        projection.keys_for_document = lambda tx, doc_id: set()
        try:
            lifecycle._transition(tx, "d1", to_history=to_history, rebuild=False)
        finally:
            projection.keys_for_document = orig
        swaps = tx.queries_containing(f"MATCH (p:{frm} {{doc_id: $doc_id}})")
        assert swaps and f"REMOVE p:{frm} SET p:{to}" in swaps[0][0]
        assert swaps[0][1]["doc_id"] == "d1" and swaps[0][1]["now"].endswith("+00:00")
        assert swaps[0][1].get("by") == ("retire" if to_history else None)


def test_transition_records_when_and_why_a_document_moved():
    from _neo4j_recorder import Recorder
    from artmind import lifecycle, projection

    orig = projection.keys_for_document
    projection.keys_for_document = lambda tx, doc_id: set()
    try:
        tx = Recorder(lambda c, p: [{"n": 0}])
        lifecycle._transition(tx, "d1", to_history=True, rebuild=False, retired_by="supersede:r1",
                              at="2026-11-16T00:00:00+00:00")
        back = Recorder(lambda c, p: [{"n": 0}])
        lifecycle._transition(back, "d1", to_history=False, rebuild=False, at="2026-11-17T00:00:00+00:00")
    finally:
        projection.keys_for_document = orig
    retires = [(c, p) for c, p in tx.calls if "REMOVE" in c and "History" in c]
    assert len(retires) == 5
    assert all(f"._retired_at = $now" in c and "._retired_by = $by" in c for c, _ in retires)
    assert all(p == {"doc_id": "d1", "now": "2026-11-16T00:00:00+00:00", "by": "supersede:r1"} for _, p in retires)
    restores = [(c, p) for c, p in back.calls if "REMOVE" in c and "History" in c]
    assert len(restores) == 5
    assert all("._revived_at = $now REMOVE" in c and "._retired_by" in c for c, _ in restores)
    assert all(p == {"doc_id": "d1", "now": "2026-11-17T00:00:00+00:00"} for _, p in restores)


def test_retire_document_passes_its_cause_to_the_transition(monkeypatch):
    from artmind import lifecycle

    seen = []
    monkeypatch.setattr(lifecycle, "plan_transition",
                        lambda doc_id, to_history: {"doc_id": doc_id, "name": None, "observations": 0, "keys": []})
    monkeypatch.setattr(lifecycle, "transition_effect", lambda plan, **kw: {})

    class _S:
        def execute_write(self, fn, *args):
            seen.append(args)
            return {"observations": 0, "projection": None, "keys": []}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(lifecycle, "neo4j_session", lambda: _S())
    lifecycle.retire_document("d1", retired_by="supersede:r1")
    assert seen == [("d1", None, "supersede:r1")]


def test_a_fresh_retire_shares_one_timestamp_with_its_record(monkeypatch, tmp_path):
    import paths
    from _neo4j_recorder import Recorder
    from artmind import lifecycle, lifecycle_records, projection

    monkeypatch.setattr(paths, "CURATION_DIR", tmp_path / "curation")
    monkeypatch.setattr(projection, "keys_for_document", lambda tx, doc_id: set())
    monkeypatch.setattr(projection, "rebuild", lambda tx, keys, **kw: {})
    tx = Recorder(lambda c, p: [{"n": 1, "domain": "pj"}])
    lifecycle._retire_tx(tx, "d1", "pj", "supersede:r1")
    record = lifecycle_records.record_for(tx, "d1", "pj")
    moves = [p for c, p in tx.calls if "REMOVE" in c and "_retired_by = $by" in c]
    assert len(moves) == 5 and {p["now"] for p in moves} == {record["recorded_at"]}
    assert {p["by"] for p in moves} == {"supersede:r1"}
