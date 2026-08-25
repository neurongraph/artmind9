"""Deterministic date parsing and document-date lifting (no Neo4j).

Phase 3 moved date lifting out of the post-write `normalize_ingested_document`
hook and into ingest itself: the projection's winner rule is "latest source
document valid_from", so every observation needs its document's date at write
time, inside the commit transaction. The pure parsing helpers below are
unchanged and are still what does the work — see
`test_observation_valid_time.py` for where they are now called from.

`apply_node_supersession` and `_stamp_chunk_valid_from` are gone. Both wrote
entity/chunk properties the projection now owns and would overwrite.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from artmind.temporal import (
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




