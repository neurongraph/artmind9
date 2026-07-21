"""Supersession application + detection unit tests."""
import inspect
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

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


def test_detect_supersession_warns_on_duplicate_version_within_title_family():
    # Two Document nodes in the same domain, same title family (dated siblings of
    # the same schedule), sharing version "2.0": the underlying Cypher query has no
    # ORDER BY, so by_version silently keeps whichever row Neo4j happened to return
    # last. That's tolerable (last-write-wins is fine), but it must be observable —
    # otherwise a SUPERSEDES edge can get wired to the wrong older document with no
    # trace. This locks in that a same-family collision logs a warning naming both
    # documents.
    docs = [
        {"id": "doc-a", "name": "interest_rate_schedule_2026.md", "version": "2.0"},
        {"id": "doc-b", "name": "interest_rate_schedule_2026_02.md", "version": "2.0"},
    ]

    @contextmanager
    def fake_neo4j_session():
        session = MagicMock()
        session.run.return_value.data.return_value = docs
        yield session

    with patch.object(t, "neo4j_session", fake_neo4j_session), \
         patch.object(t, "MARKDOWNS_DIR", __import__("pathlib").Path("/nonexistent-dir")), \
         patch.object(t.logger, "warning") as mock_warning:
        report = t.detect_supersession("banking", dry_run=True)

    assert report["domain"] == "banking"
    mock_warning.assert_called_once()
    warning_args = mock_warning.call_args.args
    assert "doc-a" in warning_args
    assert "doc-b" in warning_args


def test_detect_supersession_no_warning_for_unrelated_docs_sharing_boilerplate_version():
    # Real bug: unrelated documents (a complaint record, an incident timeline, a
    # retention decision) each independently declare version "1.0" — a boilerplate
    # initial-version value, not a real lineage signal. Flagging every such pair as
    # a "collision" is pure noise since they're different title families. Only
    # same-title-family collisions should warn (see the test above).
    docs = [
        {"id": "doc-a", "name": "case_2026_041_complaint_record.md", "version": "1.0"},
        {"id": "doc-b", "name": "case_2026_041_incident_timeline.md", "version": "1.0"},
        {"id": "doc-c", "name": "case_2026_041_retention_decision.md", "version": "1.0"},
    ]

    @contextmanager
    def fake_neo4j_session():
        session = MagicMock()
        session.run.return_value.data.return_value = docs
        yield session

    with patch.object(t, "neo4j_session", fake_neo4j_session), \
         patch.object(t, "MARKDOWNS_DIR", __import__("pathlib").Path("/nonexistent-dir")), \
         patch.object(t.logger, "warning") as mock_warning:
        report = t.detect_supersession("banking", dry_run=True)

    assert report["domain"] == "banking"
    mock_warning.assert_not_called()


def test_parse_supersession_metadata_table():
    md = (
        "| Field | Value |\n"
        "|---|---|\n"
        "| Document ID | IRS-2026-002 |\n"
        "| Version | 1.0 |\n"
        "| Effective Date | 2026-02-01 |\n"
        "| Status | Superseded |\n"
        "| Supersedes | [[interest_rate_schedule_2026]] |\n"
        "| Superseded By | [[interest_rate_schedule_2026_03]] from 2026-03-01 |\n"
    )
    out = t.parse_supersession_metadata_table(md)
    assert out == {"superseded_doc_name": "interest_rate_schedule_2026", "effective": "2026-02-01"}


def test_parse_supersession_metadata_table_none_when_supersedes_is_none():
    md = "| Field | Value |\n|---|---|\n| Version | 1.0 |\n| Supersedes | None |\n"
    assert t.parse_supersession_metadata_table(md) is None


def test_detect_supersession_resolves_metadata_table_notice_by_name(monkeypatch):
    # The real banking.reference chain: docs self-declare lineage via a metadata
    # table (`| Supersedes | [[name]] |`) instead of a prose "## Supersession
    # Notice" section, and all three share the boilerplate version "1.0" so
    # by_version can't be used to resolve them. Resolution must fall back to
    # matching the Supersedes field's document name against by_stem_name.
    docs = [
        {"id": "irs-01", "name": "interest_rate_schedule_2026.md", "version": "1.0"},
        {"id": "irs-02", "name": "interest_rate_schedule_2026_02.md", "version": "1.0"},
    ]

    class _Result:
        def data(self):
            return docs

    class FakeSession:
        def run(self, *a, **k):
            return _Result()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    bodies = {
        "interest_rate_schedule_2026.md": "| Supersedes | None |\n",
        "interest_rate_schedule_2026_02.md": (
            "| Effective Date | 2026-02-01 |\n"
            "| Supersedes | [[interest_rate_schedule_2026]] |\n"
        ),
    }
    applied = []

    monkeypatch.setattr(t, "neo4j_session", lambda: FakeSession())
    monkeypatch.setattr(t, "_read_doc_body", lambda name: bodies[name], raising=False)
    monkeypatch.setattr(
        t, "apply_supersession",
        lambda newer, older, scope, eff, detected_by: applied.append((newer, older, eff)),
    )

    report = t.detect_supersession("banking.reference", dry_run=False)

    assert applied == [("irs-02", "irs-01", "2026-02-01")]
    assert report["applied"] == [{"newer": "irs-02", "older": "irs-01", "effective": "2026-02-01"}]


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


def test_detect_supersession_only_doc_name_filters_application(monkeypatch):
    """only_doc_name applies just that doc's notice; version map still sees all docs."""
    import artmind.temporal as temporal

    docs = [
        {"id": "docA", "name": "v3.md", "version": "3.0"},
        {"id": "docB", "name": "v2.md", "version": "2.0"},
    ]

    class _Result:
        def data(self):
            return docs

    class FakeSession:
        def run(self, *a, **k):
            return _Result()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    bodies = {"v3.md": "v3 notice body", "v2.md": "v2 plain body"}
    applied = []

    monkeypatch.setattr(temporal, "neo4j_session", lambda: FakeSession())
    monkeypatch.setattr(temporal, "_read_doc_body", lambda name: bodies[name], raising=False)
    # Only the v3 body carries a supersedes notice.
    monkeypatch.setattr(
        temporal, "parse_supersession_notice",
        lambda body: {"superseded_version": "2.0", "effective": "2026-01-01"} if "notice" in body else None,
    )
    monkeypatch.setattr(
        temporal, "apply_supersession",
        lambda newer, older, scope, eff, detected_by: applied.append((newer, older)),
    )

    # Scoped to v2.md (which has no notice): nothing applies, even though v3.md's
    # notice exists — proving the APPLY loop is filtered, not the version map build.
    result = temporal.detect_supersession("d", only_doc_name="v2.md")
    assert applied == []
    assert result["applied"] == []

    # Scoped to v3.md: its notice resolves against docB and applies.
    applied.clear()
    result = temporal.detect_supersession("d", only_doc_name="v3.md")
    assert applied == [("docA", "docB")]
