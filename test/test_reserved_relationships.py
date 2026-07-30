"""LLM-extracted relationships must never mint system-managed edge types.

SUPERSEDES (document- and node-scope) is created ONLY by the audited helpers in
artmind.temporal (apply_supersession / apply_node_supersession), which always stamp
provenance (scope/detected_by/effective or detected_by/at). EXTRACTED_FROM is not
sanctioned by any shipped domain schema as an Entity<->Entity rel_type either — it's
structural, written only by this module's own Entity->DocChunk provenance code — so
it stays reserved as defense-in-depth. PRIOR_STATE is reserved too: it links a live
Entity to an :EntityVersion snapshot and is written only by artmind.entity_history,
so an LLM-minted one would imply history no snapshot node backs. PART_OF is
deliberately NOT reserved: several shipped schemas list it as a legitimate
LLM-extractable Entity->Entity relationship (e.g. "Branch X part_of Region Y"); the
only structural PART_OF edge is the hardcoded DocChunk->Document edge, a different
code path from the Entity->Entity loop below.
The two unrestricted relationship writers below — _write_to_neo4j's Entity->Entity
branch and write_user_chat's relationship loop — must skip any normalized rel_type
that falls in RESERVED_REL_TYPES rather than silently writing (or silently dropping
without a trace) unaudited lineage edges.
"""
import json

import artmind.ingest as ing


class FakeResult:
    def single(self):
        return None

    def data(self):
        return []


class FakeSession:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **kwargs):
        self.calls.append((cypher, kwargs))
        return FakeResult()


class FakeSessionCtx:
    def __init__(self, session):
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, *exc):
        return False


class FakeDriver:
    def __init__(self, session):
        self._session = session

    def session(self, database=None):
        return FakeSessionCtx(self._session)

    def close(self):
        pass


class FakeGraphDatabase:
    def __init__(self, session):
        self._session = session

    def driver(self, uri, auth=None):
        return FakeDriver(self._session)


def _stage_doc_kg_dir(tmp_path):
    (tmp_path / "document.json").write_text(
        json.dumps({"id": "d1", "name": "rates.md", "domain": "banking.test"}),
        encoding="utf-8",
    )
    (tmp_path / "chunks.json").write_text(json.dumps([]), encoding="utf-8")
    (tmp_path / "entities.json").write_text(
        json.dumps(
            [
                {"id": "e1", "name": "Rate A", "entity_class": "RATE", "domain": "banking.test"},
                {"id": "e2", "name": "Rate B", "entity_class": "RATE", "domain": "banking.test"},
                {"id": "e3", "name": "Branch X", "entity_class": "BRANCH", "domain": "banking.test"},
                {"id": "e4", "name": "Branch Y", "entity_class": "BRANCH", "domain": "banking.test"},
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "properties.json").write_text(json.dumps([]), encoding="utf-8")
    (tmp_path / "relationships.json").write_text(
        json.dumps(
            [
                {"source_name": "Rate A", "target_name": "Rate B", "rel_type": "supersedes"},
                {"source_name": "Branch X", "target_name": "Branch Y", "rel_type": "relates_to"},
            ]
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_write_to_neo4j_skips_reserved_rel_type_and_writes_normal_one(monkeypatch, tmp_path):
    doc_kg_dir = _stage_doc_kg_dir(tmp_path)

    fake_session = FakeSession()
    monkeypatch.setattr(ing, "GraphDatabase", FakeGraphDatabase(fake_session))
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda session, embedding_dim=768: None)
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", lambda session, domain, model: 0)

    warnings = []
    monkeypatch.setattr(ing.logger, "warning", lambda *a, **k: warnings.append((a, k)))

    ok = ing._write_to_neo4j(doc_kg_dir)
    assert ok is True

    rel_calls = [
        (cypher, kwargs)
        for cypher, kwargs in fake_session.calls
        if "apoc.merge.relationship" in cypher
    ]
    rel_types_written = {kwargs["type"] for _, kwargs in rel_calls}

    assert "SUPERSEDES" not in rel_types_written
    assert "RELATES_TO" in rel_types_written
    # only the non-reserved relationship should have produced a merge call
    assert len(rel_calls) == 1

    # a warning naming the reserved pair must have been logged
    assert any(
        "Rate A" in str(a) and "Rate B" in str(a) and "SUPERSEDES" in str(a)
        for a, _ in warnings
    )


def test_write_to_neo4j_still_allows_extracted_from_entity_to_docchunk(monkeypatch, tmp_path):
    """EXTRACTED_FROM is reserved for Entity->Entity, but the Entity->DocChunk
    provenance branch is the legitimate, intentional writer for that type and
    must be unaffected by the guard."""
    (tmp_path / "document.json").write_text(
        json.dumps({"id": "d1", "name": "rates.md", "domain": "banking.test"}),
        encoding="utf-8",
    )
    (tmp_path / "chunks.json").write_text(
        json.dumps([{"id": "c1", "doc_id": "d1", "text": "chunk text", "embedding": []}]),
        encoding="utf-8",
    )
    (tmp_path / "entities.json").write_text(
        json.dumps([{"id": "e1", "name": "Rate A", "entity_class": "RATE", "domain": "banking.test"}]),
        encoding="utf-8",
    )
    (tmp_path / "properties.json").write_text(json.dumps([]), encoding="utf-8")
    (tmp_path / "relationships.json").write_text(
        json.dumps(
            [{"source_name": "Rate A", "target_id": "c1", "rel_type": "extracted_from"}]
        ),
        encoding="utf-8",
    )

    fake_session = FakeSession()
    monkeypatch.setattr(ing, "GraphDatabase", FakeGraphDatabase(fake_session))
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda session, embedding_dim=768: None)
    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", lambda session, domain, model: 0)

    ok = ing._write_to_neo4j(tmp_path)
    assert ok is True

    rel_calls = [
        (cypher, kwargs)
        for cypher, kwargs in fake_session.calls
        if "apoc.merge.relationship" in cypher
    ]
    assert len(rel_calls) == 1
    assert rel_calls[0][1]["type"] == "EXTRACTED_FROM"


def test_reserved_rel_types_constant_covers_expected_types():
    """PART_OF is deliberately excluded — it's a legitimate, schema-sanctioned
    LLM-extractable Entity->Entity relationship type (see module docstring)."""
    assert ing.RESERVED_REL_TYPES == frozenset(
        {"SUPERSEDES", "EXTRACTED_FROM", "PRIOR_STATE"}
    )
    assert "PART_OF" not in ing.RESERVED_REL_TYPES


def test_prior_state_is_reserved():
    """History edges are system-managed, like SUPERSEDES and EXTRACTED_FROM.

    An LLM-extracted PRIOR_STATE edge would fabricate entity history carrying
    no snapshot node and no provenance — the same failure mode the existing
    reserved types guard against.
    """
    from artmind.ingest import RESERVED_REL_TYPES

    assert "PRIOR_STATE" in RESERVED_REL_TYPES
