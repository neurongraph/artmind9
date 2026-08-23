"""Deterministic date parsing and document-date lifting (no Neo4j)."""
import json
from unittest.mock import MagicMock, patch

import pytest

from artmind.temporal import (
    apply_node_supersession,
    canonical_entity_dates,
    parse_iso,
    lift_document_dates,
)


def test_parse_iso_full_date():
    assert parse_iso("2026-06-01") == "2026-06-01"


def test_parse_iso_human_date():
    assert parse_iso("15 March 2026") == "2026-03-15"


def test_parse_iso_partial_year():
    assert parse_iso("2026") == "2026"


def test_parse_iso_unparseable_returns_none():
    assert parse_iso("early spring") is None


def test_lift_document_dates_from_header():
    md = "# Policy\n\n**Effective Date:** 2026-06-01\n\n**Version:** 3.0\n\nBody."
    mapping = {"valid_from": ["Effective Date"], "version": ["Version"]}
    out = lift_document_dates(md, {}, mapping)
    assert out["valid_from"] == "2026-06-01"
    assert out["version"] == "3.0"
    assert out["time_source"] == "header"


def test_lift_document_dates_frontmatter_fallback():
    mapping = {"valid_from": ["Effective Date"]}
    out = lift_document_dates("Body with no header", {"date": "2024-01-01"}, mapping)
    assert out["valid_from"] == "2024-01-01"
    assert out["time_source"] == "frontmatter"


def test_lift_document_dates_uses_schema_default_with_provenance():
    out = lift_document_dates(
        "Body with no date", {}, {"valid_from": ["Effective Date"]},
        {"valid_from": "ingestion_date", "time_source": "default_ingestion", "valid_from_inferred": True},
    )
    assert parse_iso(out["valid_from"]) == out["valid_from"]
    assert out["time_source"] == "default_ingestion"
    assert out["valid_from_inferred"] is True


def test_lift_document_dates_from_metadata_table():
    # Real corpus format (verified against banking_document_corpus/policies/*.md):
    # a markdown "| Field | Value |" table, NOT colon-delimited prose.
    md = (
        "# Policy\n\n"
        "## Metadata\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Effective Date | 2026-06-01 |\n"
        "| Version | 3.0 |\n\n"
        "Body."
    )
    mapping = {"valid_from": ["Effective Date"], "version": ["Version"]}
    out = lift_document_dates(md, {}, mapping)
    assert out["valid_from"] == "2026-06-01"
    assert out["version"] == "3.0"
    assert out["time_source"] == "header"


def test_lift_document_dates_ignores_extra_table_columns():
    # A 3rd column (e.g. "Notes") after the value must not bleed into the
    # captured value — the table regex must stop at the first closing "|",
    # not the last one on the line.
    md = (
        "| Field | Value | Notes |\n"
        "|-------|-------|-------|\n"
        "| Effective Date | 2026-06-01 | Confirmed by legal |\n"
        "| Version | 3.0 | Draft |\n"
    )
    mapping = {"valid_from": ["Effective Date"], "version": ["Version"]}
    out = lift_document_dates(md, {}, mapping)
    assert out["valid_from"] == "2026-06-01"
    assert out["version"] == "3.0"


def test_lift_document_dates_version_strips_parenthetical_annotation():
    # Real corpus format (banking-corpus interest_rate_schedule_2026.md): the
    # Version header carries a trailing annotation that must not be kept
    # verbatim, or version-based supersession matching against a bare
    # "supersedes Version 1.0" notice would never match this document.
    md = (
        "# Policy\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Effective Date | 2026-06-01 |\n"
        "| Version | 1.0 (Updated Monthly) |\n\n"
        "Body."
    )
    mapping = {"valid_from": ["Effective Date"], "version": ["Version"]}
    out = lift_document_dates(md, {}, mapping)
    assert out["version"] == "1.0"


def test_lift_document_dates_version_clean_numeric_unaffected():
    # Regression guard: a clean numeric version with no parenthetical must
    # continue to lift verbatim.
    md = (
        "# Policy\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Effective Date | 2026-06-01 |\n"
        "| Version | 2.0 |\n\n"
        "Body."
    )
    mapping = {"valid_from": ["Effective Date"], "version": ["Version"]}
    out = lift_document_dates(md, {}, mapping)
    assert out["version"] == "2.0"


def test_lift_document_dates_version_non_numeric_preserved():
    # A genuinely non-numeric version scheme has no leading numeric token to
    # extract — the fix must not discard data it wasn't designed to handle.
    md = (
        "# Policy\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Effective Date | 2026-06-01 |\n"
        "| Version | Draft |\n\n"
        "Body."
    )
    mapping = {"valid_from": ["Effective Date"], "version": ["Version"]}
    out = lift_document_dates(md, {}, mapping)
    assert out["version"] == "Draft"


def test_canonical_entity_dates_valid_from():
    schema_entities = {"POLICY": {"valid_from": "effective_date"}}
    entity = {"entity_class": "POLICY", "properties": {"effective_date": "2026-01-15"}}
    out = canonical_entity_dates(entity, schema_entities, anchor=None)
    assert out["valid_from"] == "2026-01-15"
    assert out["time_source"] == "property"


def test_canonical_entity_dates_event_at():
    schema_entities = {"EVENT": {"event_at": "date_or_time"}}
    entity = {"entity_class": "EVENT", "properties": {"date_or_time": "15 March 2026"}}
    out = canonical_entity_dates(entity, schema_entities, anchor=None)
    assert out["event_at"] == "2026-03-15"


def test_canonical_entity_dates_no_mapping_returns_empty():
    entity = {"entity_class": "PERSON", "properties": {"name": "x"}}
    assert canonical_entity_dates(entity, {}, anchor=None) == {}


def test_normalize_ingested_document_merges_properties_json(tmp_path, monkeypatch):
    # Reproduces the real ingest.extract_kg() output shape: entities.json entries
    # are flat (no "properties" key); the domain-specific values live in a
    # separate properties.json keyed by entity id, merged at Neo4j-write time.
    # A prior version of normalize_ingested_document never merged properties.json,
    # so canonical_entity_dates always saw an empty properties dict and every
    # entity's temporal mapping silently no-opped.
    import artmind.temporal as temporal

    doc_kg_dir = tmp_path
    (doc_kg_dir / "document.json").write_text(
        json.dumps({"id": "doc-1", "name": "policy.md"}), encoding="utf-8"
    )
    (doc_kg_dir / "entities.json").write_text(
        json.dumps([{"id": "ent-1", "name": "Fee Policy", "entity_class": "POLICY"}]),
        encoding="utf-8",
    )
    (doc_kg_dir / "properties.json").write_text(
        json.dumps([{"id": "ent-1", "properties": {"effective_date": "2026-06-01"}}]),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        temporal, "load_schema",
        lambda domain: {
            "entity_types": {
                "POLICY": {"properties": {"effective_date": {"temporal": "valid_from"}}}
            }
        },
    )

    entity_runs = []

    class FakeResult:
        def __init__(self, matched):
            self._matched = matched

        def single(self):
            return {"matched": self._matched}

    class FakeSession:
        def run(self, cypher, **kwargs):
            if "Entity" in cypher:
                entity_runs.append((cypher, kwargs))
            return FakeResult(matched=1)

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(temporal, "neo4j_session", lambda: FakeSessionContext())

    result = temporal.normalize_ingested_document(doc_kg_dir, "banking_policy")

    assert result["entities"] == 1
    assert len(entity_runs) == 1
    cypher, kwargs = entity_runs[0]
    assert kwargs["props"]["valid_from"] == "2026-06-01"
    # Graph Entity nodes get a fresh uuid id from _upsert_entity — the staged
    # extraction id ("ent-1") never lands on any node. The hook must therefore
    # match by the same key _upsert_entity merges on: (name, entity_class, domain).
    assert "{id:" not in cypher
    assert "name:$name" in cypher and "entity_class:$entity_class" in cypher and "domain:$domain" in cypher
    assert kwargs["name"] == "Fee Policy"
    assert kwargs["entity_class"] == "POLICY"
    assert kwargs["domain"] == "banking_policy"


def test_normalize_ingested_document_counts_only_matched_entities(tmp_path, monkeypatch):
    # The written["entities"] count must reflect nodes the MATCH actually found,
    # not merely attempts — a no-op SET (e.g. entity not yet in the graph) must
    # not inflate the count.
    import artmind.temporal as temporal

    doc_kg_dir = tmp_path
    (doc_kg_dir / "document.json").write_text(
        json.dumps({"id": "doc-1", "name": "policy.md"}), encoding="utf-8"
    )
    (doc_kg_dir / "entities.json").write_text(
        json.dumps([{"id": "ent-1", "name": "Fee Policy", "entity_class": "POLICY"}]),
        encoding="utf-8",
    )
    (doc_kg_dir / "properties.json").write_text(
        json.dumps([{"id": "ent-1", "properties": {"effective_date": "2026-06-01"}}]),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        temporal, "load_schema",
        lambda domain: {
            "entity_types": {
                "POLICY": {"properties": {"effective_date": {"temporal": "valid_from"}}}
            }
        },
    )

    class FakeResult:
        def single(self):
            return {"matched": 0}

    class FakeSession:
        def run(self, cypher, **kwargs):
            return FakeResult()

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(temporal, "neo4j_session", lambda: FakeSessionContext())

    result = temporal.normalize_ingested_document(doc_kg_dir, "banking_policy")

    assert result["entities"] == 0


def test_stamp_chunk_valid_from_writes_to_chunks():
    # asof_predicate is NULL-safe, so a chunk with no valid_from always passes the
    # "in force yet?" half of the filter. Stamping it is what lets --asOf hide
    # not-yet-effective content, the counterpart to apply_supersession's valid_to.
    import artmind.temporal as temporal

    session = MagicMock()
    temporal._stamp_chunk_valid_from(session, "doc-1", "2026-06-01")

    session.run.assert_called_once()
    cypher, kwargs = session.run.call_args[0][0], session.run.call_args[1]
    assert "DocChunk" in cypher
    assert "c.valid_from" in cypher
    assert kwargs == {"docId": "doc-1", "validFrom": "2026-06-01"}


def test_stamp_chunk_valid_from_is_noop_without_a_date():
    import artmind.temporal as temporal

    session = MagicMock()
    temporal._stamp_chunk_valid_from(session, "doc-1", None)
    temporal._stamp_chunk_valid_from(session, "doc-1", "")

    session.run.assert_not_called()


def test_normalize_ingested_document_propagates_valid_from_to_chunks(tmp_path, monkeypatch):
    # Document.valid_from is stamped by normalize_*, not at _write_to_neo4j
    # (document.json carries no valid_from), so chunk propagation has to happen
    # here too or chunk-level --asOf stays half-inert.
    import artmind.temporal as temporal

    doc_kg_dir = tmp_path
    (doc_kg_dir / "document.json").write_text(
        json.dumps({"id": "doc-1", "name": "policy.md"}), encoding="utf-8"
    )
    (doc_kg_dir / "entities.json").write_text(json.dumps([]), encoding="utf-8")
    (doc_kg_dir / "properties.json").write_text(json.dumps([]), encoding="utf-8")

    monkeypatch.setattr(
        temporal, "load_schema",
        lambda domain: {
            "temporal": {
                "document": {"valid_from": ["Effective Date"]},
                "defaults": {"valid_from": "ingestion_date"},
            }
        },
    )

    calls = []

    class FakeResult:
        def single(self):
            return {"matched": 0}

    class FakeSession:
        def run(self, cypher, **kwargs):
            calls.append((cypher, kwargs))
            return FakeResult()

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(temporal, "neo4j_session", lambda: FakeSessionContext())

    temporal.normalize_ingested_document(doc_kg_dir, "banking_policy")

    chunk_writes = [(c, k) for c, k in calls if "DocChunk" in c and "c.valid_from" in c]
    assert len(chunk_writes) == 1, calls
    assert chunk_writes[0][1]["docId"] == "doc-1"
    assert chunk_writes[0][1]["validFrom"]


def test_asof_predicate_shape():
    from artmind.graph_query import asof_predicate
    pred = asof_predicate("e")
    assert "$asOf IS NULL" in pred
    assert "e.valid_from IS NULL OR e.valid_from <= $asOf" in pred
    assert "e.valid_to IS NULL OR e.valid_to > $asOf" in pred


def test_pattern1_cypher_includes_asof_when_requested():
    import artmind.graph_query as gq
    cypher, params = gq._pattern_query(
        "pattern1", {"domains": ["fiction"], "entityClass": "PERSON", "limit": 5, "asOf": "2026-07-04"}
    )
    assert "$asOf" in cypher
    assert params["asOf"] == "2026-07-04"


def test_pattern1_cypher_omits_asof_when_none():
    import artmind.graph_query as gq
    cypher, params = gq._pattern_query(
        "pattern1", {"domains": ["fiction"], "entityClass": "PERSON", "limit": 5, "asOf": None}
    )
    assert "$asOf" not in cypher


def test_pattern8_cypher_includes_asof_for_both_entities():
    # pattern8 matches (e:LABEL)-[r]-(t:Entity) — both sides are required matches,
    # so asOf must scope both, not just the anchor `e` (mirrors pattern6's
    # symmetric treatment of e1/e2).
    import artmind.graph_query as gq
    cypher, params = gq._pattern_query(
        "pattern8",
        {
            "domains": ["fiction"], "entityClass": "LOCATION",
            "entityName": "Holmes", "asOf": "2026-07-04",
        },
    )
    assert "e.valid_from" in cypher and "e.valid_to" in cypher
    assert "t.valid_from" in cypher and "t.valid_to" in cypher
    assert params["asOf"] == "2026-07-04"


def test_vector_search_cypher_includes_asof_on_chunk_leg(monkeypatch):
    import artmind.vector_query as vq

    monkeypatch.setattr(vq, "embed_question", lambda question, model=None: [0.1, 0.2])

    captured_cyphers = []

    class FakeSession:
        def run(self, cypher, **kwargs):
            captured_cyphers.append(cypher)
            return []

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(vq, "read_session", lambda: FakeSessionContext())

    vq.vector_search(["fiction"], "who is Holmes", topK=3, as_of="2026-07-04")

    assert any("node.valid_from" in c and "node.valid_to" in c for c in captured_cyphers)


def test_apply_node_supersession_writes_edge_and_retires_older():
    captured = {}

    def run_side_effect(cypher, **kwargs):
        captured["cypher"] = cypher
        captured["kwargs"] = kwargs
        mock_result = MagicMock()
        mock_result.single.return_value = {
            "newer_id": "newer-uuid", "older_id": "older-uuid", "older_name": "James Chen",
        }
        return mock_result

    with patch("artmind.temporal.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.side_effect = run_side_effect

        result = apply_node_supersession(
            newer_id="newer-uuid",
            older_id="4:abc:123",
            effective="2026-07-18",
            detected_by="user_update",
            source_chat_id="chat-1",
            reason="role holder changed",
        )

    assert result == {"newer": "newer-uuid", "older": "older-uuid", "effective": "2026-07-18"}
    cypher = captured["cypher"]
    assert "SUPERSEDES" in cypher
    assert "older.valid_to" in cypher and "older.superseded_by" in cypher
    assert "older.status" in cypher
    kwargs = captured["kwargs"]
    assert kwargs["newerId"] == "newer-uuid"
    assert kwargs["olderRef"] == "4:abc:123"
    assert kwargs["sourceChatId"] == "chat-1"
    assert kwargs["reason"] == "role holder changed"


def test_apply_node_supersession_raises_when_unresolved():
    with patch("artmind.temporal.neo4j_session") as mock_ctx:
        mock_session = mock_ctx.return_value.__enter__.return_value
        mock_session.run.return_value.single.return_value = None

        with pytest.raises(ValueError):
            apply_node_supersession(newer_id="missing", older_id="4:abc:999")
