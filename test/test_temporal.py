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


def test_pattern1_cypher_never_references_asof():
    # Phase 4: --asOf removed from pattern1 (and patterns 2-4/6-9) — the
    # projection is current by construction. _pattern_query ignores it
    # entirely now, whether or not a caller passes one.
    import artmind.graph_query as gq
    cypher, params = gq._pattern_query(
        "pattern1", {"domains": ["fiction"], "entityClass": "PERSON", "limit": 5, "asOf": "2026-07-04"}
    )
    assert "$asOf" not in cypher
    assert "asOf" not in params

    cypher, params = gq._pattern_query(
        "pattern1", {"domains": ["fiction"], "entityClass": "PERSON", "limit": 5, "asOf": None}
    )
    assert "$asOf" not in cypher


def test_pattern8_cypher_never_references_asof():
    import artmind.graph_query as gq
    cypher, params = gq._pattern_query(
        "pattern8",
        {
            "domains": ["fiction"], "entityClass": "LOCATION",
            "entityName": "Holmes", "asOf": "2026-07-04",
        },
    )
    assert "valid_from" not in cypher and "valid_to" not in cypher
    assert "asOf" not in params
    # Entity-to-Entity relationships are RELATES_TO now (Phase 4).
    assert "MATCH (e:LOCATION)-[r:RELATES_TO]-(t:Entity)" in cypher


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






# ── temporal.document_patterns: dates that live outside any label ──────────
# A daily note (`2026-01-07.md`) carries its date only in the filename and as
# a bare first line -- no `Date:` label, no frontmatter key -- so the label
# mapping alone always fell through to the ingestion-date default.

_JOURNAL_DEFAULTS = {"valid_from": "ingestion_date", "time_source": "default_ingestion"}


def test_lift_document_dates_from_filename_pattern():
    out = lift_document_dates(
        "2026-01-07\n\nGovernance call today.", {}, {"valid_from": ["Date", "date"]},
        _JOURNAL_DEFAULTS,
        patterns={"valid_from": [{"filename": r"^(\d{4}-\d{2}-\d{2})$"}]},
        filename="2026-01-07.md",
    )
    assert out["valid_from"] == "2026-01-07"
    assert out["time_source"] == "filename"
    assert "valid_from_inferred" not in out


def test_lift_document_dates_from_first_line_pattern():
    out = lift_document_dates(
        "\n# 7 January 2026\n\nBody.", {}, {"valid_from": ["Date"]}, _JOURNAL_DEFAULTS,
        patterns={"valid_from": [{"first_line": r"(\d{1,2} [A-Za-z]+ \d{4})"}]},
        filename="untitled.md",
    )
    assert out["valid_from"] == "2026-01-07"
    assert out["time_source"] == "first_line"


def test_lift_document_dates_patterns_tried_in_order_after_labels():
    patterns = {"valid_from": [
        {"filename": r"^(\d{4}-\d{2}-\d{2})"},
        {"first_line": r"(\d{4}-\d{2}-\d{2})"},
    ]}
    # The filename doesn't match, so the second pattern answers.
    out = lift_document_dates("2026-02-03\n", {}, {"valid_from": ["Date"]}, None,
                              patterns=patterns, filename="notes.md")
    assert (out["valid_from"], out["time_source"]) == ("2026-02-03", "first_line")
    # A label always wins over a pattern.
    out = lift_document_dates("Date: 2025-12-31\n", {}, {"valid_from": ["Date"]}, None,
                              patterns=patterns, filename="2026-01-07.md")
    assert (out["valid_from"], out["time_source"]) == ("2025-12-31", "header")


def test_lift_document_dates_pattern_match_must_parse_as_a_date():
    # A capture that isn't a date is skipped, not stored raw: a pattern is
    # only ever a guess about where a date might be.
    out = lift_document_dates(
        "Body.", {}, {"valid_from": ["Date"]}, _JOURNAL_DEFAULTS,
        patterns={"valid_from": [{"filename": r"^(\w+)"}]}, filename="meeting.md",
    )
    assert out["time_source"] == "default_ingestion"


def test_lift_document_dates_pattern_without_label_mapping():
    out = lift_document_dates(
        "Body.", {}, {}, None,
        patterns={"valid_from": [{"filename": r"(\d{4}-\d{2}-\d{2})"}]}, filename="log 2026-03-04.md",
    )
    assert out == {"valid_from": "2026-03-04", "time_source": "filename"}


def test_lift_document_dates_ignores_malformed_pattern_entries():
    out = lift_document_dates(
        "2026-01-07\n", {}, {}, None,
        patterns={"valid_from": ["not-a-dict", {"bogus_source": "x"}, {"first_line": r"(\d{4}-\d{2}-\d{2})"}]},
        filename="x.md",
    )
    assert out["valid_from"] == "2026-01-07"


def test_load_schema_child_document_patterns_override_parent(tmp_path):
    from artmind.temporal import load_schema

    (tmp_path / "journal_schema.yaml").write_text(
        "temporal:\n  document_patterns:\n    valid_from:\n    - filename: 'a'\n", encoding="utf-8")
    (tmp_path / "journal.work_schema.yaml").write_text(
        "temporal:\n  document_patterns:\n    valid_from:\n    - first_line: 'b'\n", encoding="utf-8")
    merged = load_schema("journal.work", schemas_dir=tmp_path)["temporal"]
    assert merged["document_patterns"] == {"valid_from": [{"first_line": "b"}]}
    (tmp_path / "journal.home_schema.yaml").write_text("temporal: {}\n", encoding="utf-8")
    inherited = load_schema("journal.home", schemas_dir=tmp_path)["temporal"]
    assert inherited["document_patterns"] == {"valid_from": [{"filename": "a"}]}


def test_relative_anchor_date_from_document_valid_from():
    from artmind.temporal import relative_anchor_date

    schema = {"temporal": {"relative_anchor": "document.valid_from",
                           "defaults": {"time_source": "default_ingestion"}}}
    assert relative_anchor_date(schema, {"_valid_from": "2026-01-07", "_valid_time_source": "filename"}) == "2026-01-07"
    # An ingestion-date default anchors nothing.
    assert relative_anchor_date(schema, {"_valid_from": "2026-10-10", "_valid_time_source": "default_ingestion"}) is None
    # No anchor declared, or no date at all.
    assert relative_anchor_date({}, {"_valid_from": "2026-01-07", "_valid_time_source": "filename"}) is None
    assert relative_anchor_date(schema, {}) is None


def test_date_is_only_the_default():
    from artmind.temporal import date_is_only_the_default

    assert date_is_only_the_default({}, {"_valid_time_source": "default_ingestion"})
    custom = {"temporal": {"defaults": {"time_source": "my_default"}}}
    assert date_is_only_the_default(custom, {"_valid_time_source": "my_default"})
    assert not date_is_only_the_default(custom, {"_valid_time_source": "default_ingestion"})
    assert not date_is_only_the_default({}, {"_valid_time_source": "filename"})


# ── document dates as contracts write them (end to end: all four fell back) ──

import pytest as _pytest

from artmind.temporal import find_date, lift_document_dates, parse_iso

_LABELS = {"valid_from": ["Dated", "Amendment Date", "Effective Date", "Date"]}
_DEFAULTS = {"valid_from": "ingestion_date", "time_source": "default_ingestion"}


@_pytest.mark.parametrize("value, iso", [
    ("01/01/2026", "2026-01-01"), ("12/12/2025", "2025-12-12"), ("1.4.2025", "2025-04-01"),
    ("12/31/2025", "2025-12-31"),  # only month-first is valid
    ("31/02/2025", None), ("1st April 2025", "2025-04-01"),
])
def test_parse_iso_reads_numeric_dates_day_first(value, iso):
    assert parse_iso(value) == iso


def test_find_date_takes_the_first_readable_date_inside_a_longer_value():
    assert find_date("September 2025 29 September 2025 | 01:15:03 PDT") == "2025-09-29"
    assert find_date("no date here") is None


@_pytest.mark.parametrize("body, iso", [
    ("Dated 01/01/2026\nPARTY A\nPARTY B\n", "2026-01-01"),  # label, space, value
    ("Dated               September 2025 29 September 2025 | 01:15:03 PDT\n", "2025-09-29"),
    ("| Dated | 12/12/2025 |\n", "2025-12-12"),
    ("**Effective Date:** 1 August 2025\n", "2025-08-01"),
])
def test_a_header_date_is_read_in_the_layouts_contracts_use(body, iso):
    assert lift_document_dates(body, {}, _LABELS, _DEFAULTS)["valid_from"] == iso


def test_an_empty_signature_block_date_is_not_the_documents_date():
    """`Date:` then a blank line used to capture the next line's "Date:" and
    store it raw as the document date."""
    body = "Dated 1 July 2017\n\nSigned\nDate:\n\nDate:\n"
    assert lift_document_dates(body, {}, _LABELS, _DEFAULTS)["valid_from"] == "2017-07-01"

    undated = "Signed\nDate:\n\nDate:\n"
    out = lift_document_dates(undated, {}, _LABELS, _DEFAULTS)
    assert out["time_source"] == "default_ingestion", "no readable date: the default, never a raw label value"
