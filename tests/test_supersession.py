"""Supersession application + detection unit tests."""
import inspect
import artmind.temporal as t


def test_apply_supersession_signature():
    sig = inspect.signature(t.apply_supersession)
    for p in ("newer_doc_id", "older_doc_id", "scope", "effective"):
        assert p in sig.parameters


def test_detect_supersession_notice_parses_version():
    md = (
        "## Supersession Notice\n\n"
        "This document (Version 3.0) supersedes Version 2.0 "
        "(Effective Date: 2026-01-15), effective 2026-06-01.\n"
    )
    out = t.parse_supersession_notice(md)
    assert out is not None
    assert out["superseded_version"] == "2.0"
    assert out["effective"] == "2026-06-01"


def test_detect_supersession_notice_parses_intervening_words():
    # Real fixture phrasing (banking_document_corpus/policies/policy_complaints_v3.md,
    # line 24): "supersedes and replaces Version 2.0" — words between "supersedes"
    # and "Version" broke a tight `supersedes?\s+Version` regex during plan review
    # (verified by running that regex against the actual file: no match). This test
    # locks in the real phrasing so a regression can't reintroduce the tight version.
    md = (
        "**This policy (Version 3.0, effective 2026-06-01) supersedes and replaces "
        "Version 2.0 (effective 2026-01-15) in full.**"
    )
    out = t.parse_supersession_notice(md)
    assert out is not None
    assert out["superseded_version"] == "2.0"
    assert out["effective"] == "2026-06-01"


def test_detect_supersession_notice_ignores_metadata_table_dates():
    # Reproduces a second real bug found during plan review: the actual document
    # body includes a "## Metadata" table BEFORE the "## Supersession Notice"
    # section, and that table's "| Supersedes | Version 2.0 (Effective Date
    # 2026-01-15) |" row also contains the words "Supersedes" and "Effective Date".
    # An unscoped whole-body regex search picks up THAT date (2026-01-15, the OLD
    # version's date) instead of the Supersession Notice section's own date
    # (2026-06-01) — verified by running the whole-document search against the
    # real fixture file: it returned 2026-01-15, the wrong value. Parsing must be
    # scoped to the "## Supersession Notice" section, not the whole document body.
    md = (
        "## Metadata\n\n"
        "| Field | Value |\n"
        "|-------|-------|\n"
        "| Version | 3.0 |\n"
        "| Effective Date | 2026-06-01 |\n"
        "| Supersedes | Version 2.0 (Effective Date 2026-01-15) |\n\n"
        "## Supersession Notice\n\n"
        "**This policy (Version 3.0, effective 2026-06-01) supersedes and replaces "
        "Version 2.0 (effective 2026-01-15) in full.**\n\n"
        "## Executive Summary\n\nBody."
    )
    out = t.parse_supersession_notice(md)
    assert out is not None
    assert out["superseded_version"] == "2.0"
    assert out["effective"] == "2026-06-01"
