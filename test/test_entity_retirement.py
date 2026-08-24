"""Superseding a document must take its entities with it.

The old mechanism guessed. `_retire_orphaned_entities` stamped `valid_to`,
`superseded_by` and `status='superseded'` onto entities it believed the
superseded document solely sourced, using a `size(docIds) = 1` heuristic. On
the live corpus it fired on 2 of 5 supersessions and left **235 entities live**
whose only source was a superseded document — and it failed silently, because
an empty Cypher MATCH raises nothing.

There is no guess any more. Superseding retires the document
(`artmind.lifecycle`), which moves its observations to `history`; the
projection then deletes any aggregate key with zero `latest` observations. That
is not a heuristic about sourcing — it is an arithmetic fact about what is
still asserted.
"""
from unittest.mock import MagicMock

import pytest

import artmind.temporal as t


def test_the_single_source_heuristic_is_gone():
    assert not hasattr(t, "_retire_orphaned_entities")
    assert not hasattr(t, "apply_node_supersession")
    assert not hasattr(t, "_stamp_chunk_valid_from")


def test_document_supersession_retires_the_older_document(monkeypatch):
    retired: list = []
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda doc_id, *a, **k: retired.append(doc_id))

    session = MagicMock()
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    monkeypatch.setattr(t, "neo4j_session", lambda *a, **k: ctx)

    t.apply_supersession("newer-id", "older-id", scope="document", effective="2026-03-01")

    assert retired == ["older-id"], "the older document's assertions must leave `latest`"


def test_node_scoped_supersession_does_not_retire_a_whole_document(monkeypatch):
    """Retirement is document-granular by nature."""
    retired: list = []
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda doc_id, *a, **k: retired.append(doc_id))

    session = MagicMock()
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    monkeypatch.setattr(t, "neo4j_session", lambda *a, **k: ctx)

    t.apply_supersession("newer-id", "older-id", scope="node", effective="2026-03-01")

    assert retired == []


def test_supersession_no_longer_writes_projection_owned_entity_properties(monkeypatch):
    """`valid_to`, `superseded_by` and `status` on an :Entity are derived now.
    Writing them here would have them wiped by the next rebuild."""
    calls: list = []
    session = MagicMock()
    session.run.side_effect = lambda cypher, **kw: calls.append(cypher) or MagicMock()
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    monkeypatch.setattr(t, "neo4j_session", lambda *a, **k: ctx)
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda *a, **k: None)

    t.apply_supersession("newer-id", "older-id", scope="document", effective="2026-03-01")

    for cypher in calls:
        assert "e.status" not in cypher
        assert "superseded_by = newer.id" not in cypher or ":Document" in cypher
        assert "MATCH (c0:DocChunk" not in cypher, "the single-source guard must be gone"


# ── the rule that replaced it ───────────────────────────────────────────────


def test_retire_demotes_observations_and_rebuilds_the_keys_it_touched(monkeypatch):
    """Assert on the parameters actually sent, and on which queries ran."""
    from artmind import lifecycle

    calls: list = []

    class _Tx:
        def run(self, cypher, **kw):
            calls.append((cypher, kw))
            result = MagicMock()
            result.single.return_value = {"n": 3, "c": 0}
            result.data.return_value = [{"key": "alice|PERSON|general"}]
            return result

    tx = _Tx()
    session = MagicMock()
    session.execute_write.side_effect = lambda fn, *a, **k: fn(tx, *a, **k)
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    monkeypatch.setattr(lifecycle, "neo4j_session", lambda *a, **k: ctx)

    rebuilt: list = []
    monkeypatch.setattr(
        "artmind.projection.rebuild", lambda tx, keys, **kw: rebuilt.append(sorted(keys)) or {}
    )

    result = lifecycle.retire_document("doc-1")

    demote = [(c, kw) for c, kw in calls if "SET o._status = $to_status" in c]
    assert len(demote) == 1
    assert demote[0][1]["doc_id"] == "doc-1"
    assert demote[0][1]["from_status"] == "latest"
    assert demote[0][1]["to_status"] == "history"
    assert result["observations"] == 3

    # ...and the keys it touched are handed to the projection, which owns
    # whether an entity survives.
    assert rebuilt == [[("alice", "PERSON", "general")]]


def test_restore_is_the_exact_inverse(monkeypatch):
    from artmind import lifecycle

    calls: list = []

    class _Tx:
        def run(self, cypher, **kw):
            calls.append((cypher, kw))
            result = MagicMock()
            result.single.return_value = {"n": 3, "c": 0}
            result.data.return_value = []
            return result

    tx = _Tx()
    session = MagicMock()
    session.execute_write.side_effect = lambda fn, *a, **k: fn(tx, *a, **k)
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    monkeypatch.setattr(lifecycle, "neo4j_session", lambda *a, **k: ctx)
    monkeypatch.setattr("artmind.projection.rebuild", lambda tx, keys, **kw: {})

    lifecycle.restore_document("doc-1")

    demote = [kw for c, kw in calls if "SET o._status = $to_status" in c]
    assert demote[0]["from_status"] == "history"
    assert demote[0]["to_status"] == "latest"


def test_retire_writes_no_dates_anywhere(monkeypatch):
    """Retiring is an assertion-time act. A retired document's facts keep the
    valid-time window they always had — conflating the two axes is the most
    common modelling error in this system."""
    from artmind import lifecycle

    calls: list = []

    class _Tx:
        def run(self, cypher, **kw):
            calls.append(cypher)
            result = MagicMock()
            result.single.return_value = {"n": 0, "c": 0}
            result.data.return_value = []
            return result

    tx = _Tx()
    session = MagicMock()
    session.execute_write.side_effect = lambda fn, *a, **k: fn(tx, *a, **k)
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    monkeypatch.setattr(lifecycle, "neo4j_session", lambda *a, **k: ctx)
    monkeypatch.setattr("artmind.projection.rebuild", lambda tx, keys, **kw: {})

    lifecycle.retire_document("doc-1")

    for cypher in calls:
        assert "valid_from" not in cypher
        assert "valid_to" not in cypher
