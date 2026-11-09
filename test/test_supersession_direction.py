"""Supersession direction from document metadata, then _valid_from; never version numbers."""
from unittest.mock import patch

import artmind.conflicts as c
import artmind.temporal as t
from artmind.supersession_direction import decide

KYC_OLD = {"id": "old", "name": "policy_customer_identification.md", "_valid_from": "2026-01-01", "version": "2.1",
           "meta": {"Status": "Superseded", "Superseded By": "[[policy_customer_identification_v2]] from 2026-03-01"}}
KYC_NEW = {"id": "new", "name": "policy_customer_identification_v2.md", "_valid_from": "2026-03-01", "version": "2.0",
           "meta": {"Status": "Active", "Supersedes": "Version 1.0, [[policy_customer_identification]]"}}


def test_explicit_links_win_over_version_numbers():
    assert decide(KYC_OLD, KYC_NEW) == (KYC_NEW, KYC_OLD)
    assert decide(KYC_NEW, KYC_OLD) == (KYC_NEW, KYC_OLD)


def test_one_sided_link_is_enough():
    a = {**KYC_OLD, "meta": {}}
    assert decide(a, KYC_NEW) == (KYC_NEW, a)


def test_status_only():
    a = {"id": "a", "name": "a.md", "meta": {"Status": "Superseded"}}
    b = {"id": "b", "name": "b.md", "meta": {"Status": "Current"}}
    assert decide(a, b) == (b, a)
    assert decide(b, a) == (b, a)


def test_dates_only():
    a = {"id": "a", "name": "a.md", "_valid_from": "2026-01-01", "meta": {}}
    b = {"id": "b", "name": "b.md", "_valid_from": "2026-02-01", "meta": {}}
    assert decide(a, b) == (b, a)


def test_conflicting_explicit_links_are_ambiguous():
    a = {"id": "a", "name": "a.md", "meta": {"Supersedes": "[[b]]"}}
    b = {"id": "b", "name": "b.md", "meta": {"Supersedes": "[[a]]"}}
    assert decide(a, b) is None


def test_equal_or_missing_dates_and_no_metadata_is_none():
    a = {"id": "a", "name": "a.md", "_valid_from": "2026-01-01", "meta": {}}
    b = {"id": "b", "name": "b.md", "_valid_from": "2026-01-01", "meta": {}}
    assert decide(a, b) is None
    assert decide({"id": "a", "name": "a.md"}, {"id": "b", "name": "b.md"}) is None


def test_version_numbers_are_ignored():
    a = {"id": "a", "name": "a.md", "version": "9.0", "meta": {}}
    b = {"id": "b", "name": "b.md", "version": "1.0", "meta": {}}
    assert decide(a, b) is None


class _Rec:
    def __init__(self, row):
        self.row = row

    def single(self):
        return self.row

    def data(self):
        return [self.row]


class _Session:
    def __init__(self, row):
        self.row, self.calls = row, []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return _Rec(self.row)


PAIR = {"id_a": "eA", "id_b": "eB", "domain_a": "d", "domain_b": "d", "entity_class": "POLICY", "name_a": "x", "name_b": "x"}
VERDICT = {"verdict": "superseded", "aspect": "x", "claim_a": "o", "claim_b": "n", "severity": "low"}


def _row(af, bf):
    return {"a": "docA", "an": "a.md", "ap": None, "af": af, "av": "1", "b": "docB", "bn": "b.md", "bp": None, "bf": bf, "bv": "1"}


def test_adjudicator_applies_the_later_document_as_newer(monkeypatch):
    applied = []
    monkeypatch.setattr(c, "apply_supersession", lambda *a, **k: applied.append((a, k)))
    session = _Session(_row("2026-03-01", "2026-01-01"))
    with patch("artmind.supersession_direction.meta_for", return_value={}):
        c.materialize(session, PAIR, VERDICT, [], [], "m")
    assert applied == [(("docA", "docB", "document", "2026-03-01"), {"detected_by": "adjudicator"})]
    assert "coalesce(da._valid_from, da.valid_from)" in session.calls[0][0]


def test_adjudicator_applies_nothing_when_direction_is_unknown(monkeypatch):
    applied = []
    monkeypatch.setattr(c, "apply_supersession", lambda *a, **k: applied.append(a))
    with patch("artmind.supersession_direction.meta_for", return_value={}), patch.object(c.logger, "warning") as warn:
        c.materialize(_Session(_row(None, None)), PAIR, VERDICT, [], [], "m")
    assert applied == [] and warn.called


def test_doc_validity_reads_underscore_valid_from():
    session = _Session({"valid_from": "2026-03-01", "version": "2.0"})
    assert c._doc_validity(session, [{"doc_id": "d1"}]) == ("2026-03-01", "2.0")
    assert "coalesce(d._valid_from, d.valid_from) AS valid_from" in session.calls[0][0]


def test_detect_supersession_reads_underscore_valid_from_and_skips_contradicting_pair(monkeypatch):
    docs = [
        {"id": "old", "name": "x.md", "version": "2.1", "valid_from": "2026-01-01"},
        {"id": "new", "name": "x_v2.md", "version": "2.0", "valid_from": "2026-03-01"},
    ]
    sessions = []

    class S:
        def run(self, cypher, **k):
            sessions.append(cypher)
            return type("R", (), {"data": lambda self: docs})()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    # the old document's body claims it supersedes the new one (wrong direction vs the new one's metadata)
    bodies = {
        "x.md": "| Supersedes | [[x_v2]] |\n| Superseded By | None |\n",
        "x_v2.md": "| Superseded By | [[x]] |\n",
    }
    applied = []
    monkeypatch.setattr(t, "neo4j_session", lambda: S())
    monkeypatch.setattr(t, "_read_doc_body", lambda name: bodies[name], raising=False)
    monkeypatch.setattr(t, "apply_supersession", lambda *a, **k: applied.append(a))

    report = t.detect_supersession("d", dry_run=False)

    assert "coalesce(d._valid_from, d.valid_from) AS valid_from" in sessions[0]
    # x.md says it supersedes x_v2, x_v2.md says it is superseded by x: consistent, x newer -> applied
    assert applied and applied[0][:2] == ("old", "new")

    # now contradict: x_v2 claims to supersede x as well
    bodies["x_v2.md"] = "| Supersedes | [[x]] |\n"
    applied.clear()
    report = t.detect_supersession("d", dry_run=False)
    assert applied == []
    assert report["skipped"][0]["reason"] == "direction contradicts metadata"


def test_dotted_version_stem_matches_exactly():
    a = {"id": "a", "name": "policy_v2.0.md", "meta": {}}
    b = {"id": "b", "name": "policy_v1.md", "meta": {"Supersedes": "[[policy_v2.0]]"}}
    assert decide(a, b) == (b, a)
    c = {"id": "c", "name": "policy_v2.md", "meta": {}}
    assert decide(c, b) is None  # [[policy_v2.0]] must not match policy_v2.md


def test_link_case_and_md_suffix_are_ignored():
    a = {"id": "a", "name": "policy_x.md", "meta": {}}
    b = {"id": "b", "name": "other.md", "meta": {"Supersedes": "[[Policy_X]]"}}
    assert decide(a, b) == (b, a)


def test_dates_compare_the_iso_date_only():
    a = {"id": "a", "name": "a.md", "_valid_from": "2026-03-01T00:00:00", "meta": {}}
    b = {"id": "b", "name": "b.md", "_valid_from": "2026-03-01", "meta": {}}
    assert decide(a, b) is None


def _family_session(docs):
    class S:
        def run(self, cypher, **k):
            return type("R", (), {"data": lambda self: docs})()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return S()


def _family(monkeypatch, bodies):
    docs = [
        {"id": "jan", "name": "report_2026_01.md", "version": None, "valid_from": "2026-01-01"},
        {"id": "mar", "name": "report_2026_03.md", "version": None, "valid_from": "2026-03-01"},
    ]
    applied = []
    monkeypatch.setattr(t, "neo4j_session", lambda: _family_session(docs))
    monkeypatch.setattr(t, "_read_doc_body", lambda name: bodies.get(name, ""), raising=False)
    monkeypatch.setattr(t, "load_schema", lambda d: {"temporal": {"defaults": {"supersede_on_title_family": True}}})
    monkeypatch.setattr(t, "_infer_family_supersessions", lambda docs, only: [(docs[1], docs[0], "2026-03-01")])
    monkeypatch.setattr(t, "apply_supersession", lambda *a, **k: applied.append(a))
    return applied


def test_title_family_pair_applied_when_metadata_absent_and_dates_differ(monkeypatch):
    applied = _family(monkeypatch, {})
    report = t.detect_supersession("d", dry_run=False)
    assert applied and applied[0][:2] == ("mar", "jan")
    assert "skipped" not in report


def test_title_family_pair_skipped_when_metadata_picks_the_opposite_direction(monkeypatch):
    applied = _family(monkeypatch, {"report_2026_01.md": "| Status | Active |\n", "report_2026_03.md": "| Status | Superseded |\n"})
    report = t.detect_supersession("d", dry_run=False)
    assert applied == []
    assert report["applied"] == []
    assert report["skipped"][0]["reason"] == "direction contradicts metadata"


def test_adjudicator_returns_skip_report_when_direction_undecided(monkeypatch):
    monkeypatch.setattr(c, "apply_supersession", lambda *a, **k: None)
    with patch("artmind.supersession_direction.meta_for", return_value={}):
        out = c.materialize(_Session(_row(None, None)), PAIR, VERDICT, [], [], "m")
    assert out == {"skipped": "supersession direction undecided", "a": "docA", "b": "docB"}


def test_detect_conflicts_report_surfaces_undecided_verdicts(monkeypatch, tmp_path):
    import json
    item = {"pair": PAIR, "verdict": VERDICT, "evidence_a": [], "evidence_b": []}
    f = tmp_path / "in.json"
    f.write_text(json.dumps({"conflicts": [item]}))
    monkeypatch.setattr(c, "materialize", lambda *a, **k: {"skipped": "supersession direction undecided", "a": "x", "b": "y"})
    monkeypatch.setattr(c, "neo4j_session", lambda: _family_session([]))
    report = c.detect_conflicts(["d"], from_file=str(f))
    assert report["skipped"] == [{"skipped": "supersession direction undecided", "a": "x", "b": "y"}]
    assert report["conflicts"] == [] and report["stats"] == {"materialized": 0}
