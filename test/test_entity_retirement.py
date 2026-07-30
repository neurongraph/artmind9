"""Document supersession must retire entities the superseded document solely sourced."""

import artmind.temporal as t


class _Rec:
    def __init__(self, data):
        self._data = data

    def single(self):
        return self._data

    def data(self):
        return [self._data] if self._data else []


class FakeSession:
    """Records every Cypher statement and its parameters."""

    def __init__(self):
        self.runs = []

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        return _Rec({"n": 1})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_retire_orphaned_entities_uses_single_source_guard():
    """Only entities whose entire evidence is the superseded document may retire.

    An entity the newer document re-asserts has EXTRACTED_FROM edges to both
    documents by the time this runs, and an entity with an unrelated live
    source likewise — both must survive, which the size(docIds) = 1 test is
    what enforces.
    """
    session = FakeSession()

    t._retire_orphaned_entities(session, "older-doc", "newer-doc", "2026-06-01")

    assert len(session.runs) == 1
    cypher, kwargs = session.runs[0]
    assert "size(docIds) = 1" in cypher
    assert "DocChunk {doc_id: $olderDocId}" in cypher
    assert "DISTINCT" in cypher
    assert "coalesce(e.valid_to, $effective)" in cypher
    assert kwargs["olderDocId"] == "older-doc"
    assert kwargs["newerDocId"] == "newer-doc"
    assert kwargs["effective"] == "2026-06-01"


def test_retire_orphaned_entities_noop_without_effective_date():
    """Without an effective date there is no validity boundary to stamp.

    apply_supersession already tolerates a null effective for the document
    stamp (coalesce keeps the old value); retiring entities to a null valid_to
    would set nothing while still writing status, so skip entirely.
    """
    session = FakeSession()

    t._retire_orphaned_entities(session, "older-doc", "newer-doc", None)

    assert session.runs == []


def test_apply_supersession_retires_entities_for_document_scope(monkeypatch):
    """All three supersession routes converge here, so retirement belongs here.

    Gated on document scope, matching the existing chunk stamp: a section- or
    clause-scoped supersession does not retire whole entities.
    """
    session = FakeSession()
    monkeypatch.setattr(t, "neo4j_session", lambda: session)
    calls = []
    monkeypatch.setattr(
        t, "_retire_orphaned_entities",
        lambda s, older, newer, eff: calls.append((older, newer, eff)),
    )

    t.apply_supersession("newer-doc", "older-doc", "document", "2026-06-01")

    assert calls == [("older-doc", "newer-doc", "2026-06-01")]


def test_apply_supersession_skips_retirement_for_non_document_scope(monkeypatch):
    """Sub-document scopes retire no entities — there is no sub-document unit."""
    session = FakeSession()
    monkeypatch.setattr(t, "neo4j_session", lambda: session)
    calls = []
    monkeypatch.setattr(
        t, "_retire_orphaned_entities",
        lambda s, older, newer, eff: calls.append((older, newer, eff)),
    )

    t.apply_supersession("newer-doc", "older-doc", "section", "2026-06-01")

    assert calls == []
