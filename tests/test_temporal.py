"""Deterministic date parsing and document-date lifting (no Neo4j)."""
from artmind.temporal import parse_iso, lift_document_dates


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
