"""Deterministic date parsing and document-date lifting (no Neo4j)."""
import json

from artmind.temporal import canonical_entity_dates, parse_iso, lift_document_dates


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
        lambda domain: {"temporal": {"entities": {"POLICY": {"valid_from": "effective_date"}}}},
    )

    written_props = {}

    class FakeSession:
        def run(self, cypher, **kwargs):
            if "Entity" in cypher:
                written_props.update(kwargs.get("props", {}))

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(temporal, "neo4j_session", lambda: FakeSessionContext())

    result = temporal.normalize_ingested_document(doc_kg_dir, "banking_policy")

    assert result["entities"] == 1
    assert written_props["valid_from"] == "2026-06-01"
