"""Supersession assertions travel as curation records (spec 2026-09-26 §15
A10). No Neo4j: recording sessions stand in; every test asserts on the
Cypher and parameters sent (CLAUDE.md §3)."""
import hashlib
import json

import pytest

from artmind import curation_records, supersession_records


@pytest.fixture(autouse=True)
def curation_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def data(self):
        return list(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, domains=("banking.ops",), older_ids=()):
        self.calls = []
        self._domains = domains
        self._older_ids = older_ids

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if "RETURN DISTINCT d._domain AS domain" in cypher:
            return _Result({"domain": d} for d in self._domains)
        if "RETURN oid" in cypher:
            return _Result({"oid": o} for o in self._older_ids)
        return _Result()


def _record(**overrides):
    record = supersession_records.record_for(_Session(), "new-doc", "old-doc", "document", "2026-03-01", "manual")
    record.update(overrides)
    return record


def test_a_supersession_record_is_keyed_by_newer_older_and_scope():
    record = _record()

    assert record.pop("recorded_at").endswith("+00:00")
    assert record == {
        "id": hashlib.sha1(b"new-doc|old-doc|document").hexdigest(),
        "newer_doc_id": "new-doc", "older_doc_id": "old-doc", "scope": "document",
        "effective": "2026-03-01", "detected_by": "manual", "domains": ["banking.ops"],
    }
    assert supersession_records.record_id("new-doc", "old-doc", "node") != record["id"]


def test_apply_merges_the_edge_then_derives_valid_to_from_every_edge():
    tx = _Session()
    record = _record()

    assert supersession_records.apply(tx, record, "fp") == set()

    (merge, merge_params), (recompute, recompute_params) = tx.calls
    assert "MERGE (newer)-[s:SUPERSEDES {scope: $scope}]->(older)" in merge
    assert "OPTIONAL MATCH (o2:DocumentHistory {id: $older})" in merge, "a retired older document still gets its edge"
    assert merge_params == {
        "newer": "new-doc", "older": "old-doc", "scope": "document", "effective": "2026-03-01",
        "detected_by": "manual", "id": record["id"], "fingerprint": "fp",
    }
    assert "ORDER BY coalesce(s.effective, '') DESC, newer.id DESC" in recompute
    assert "older._valid_to = head([e IN edges WHERE e.effective IS NOT NULL | e.effective])" in recompute
    assert "REMOVE older.valid_to" in recompute, "one name (spec 2026-11-15 §7.1)"
    assert "SET older.valid_to" not in recompute and "older.valid_to =" not in recompute
    assert "older.superseded_by = CASE WHEN size(edges) = 0 THEN null" in recompute
    assert recompute_params == {"older": "old-doc"}


def test_remove_deletes_the_edge_and_re_derives_valid_to_from_what_remains():
    tx = _Session()

    supersession_records.remove(tx, _record())

    (delete, params), (recompute, recompute_params) = tx.calls
    assert "MATCH (newer)-[s:SUPERSEDES {scope: $scope}]->(older)" in delete and "DELETE s" in delete
    assert params == {"newer": "new-doc", "older": "old-doc", "scope": "document"}
    assert recompute_params == {"older": "old-doc"}


def test_remove_of_an_unreadable_record_matches_the_edge_by_record_id_then_recomputes_each_older_doc():
    tx = _Session(older_ids=["old-a", "old-b", "old-a"])

    supersession_records.remove(tx, {"id": "abc"})

    (delete, params), *recomputes = tx.calls
    assert "MATCH ()-[s:SUPERSEDES {record_id: $id}]->(o)" in delete
    assert "WITH s, o.id AS oid" in delete and "DELETE s" in delete and "RETURN oid" in delete
    assert params == {"id": "abc"}
    assert all("older.superseded_by = CASE" in c for c, _ in recomputes)
    assert sorted(p["older"] for _, p in recomputes) == ["old-a", "old-b"], "each distinct older document, once"
    assert all(set(p) == {"older"} for _, p in recomputes)


def test_remove_of_an_unreadable_record_that_matches_no_edge_recomputes_nothing():
    tx = _Session(older_ids=[])

    supersession_records.remove(tx, {"id": "abc"})

    assert [p for _, p in tx.calls] == [{"id": "abc"}]


@pytest.mark.parametrize("missing", ["scope", "newer_doc_id", "older_doc_id"])
def test_remove_of_a_record_missing_a_content_field_falls_back_to_the_record_id(missing):
    tx = _Session(older_ids=["old-doc"])
    record = _record()
    del record[missing]

    supersession_records.remove(tx, record)

    (delete, params), (recompute, recompute_params) = tx.calls
    assert "SUPERSEDES {record_id: $id}" in delete and "scope" not in params
    assert params == {"id": record["id"]}
    assert recompute_params == {"older": "old-doc"}


def test_the_supersessions_kind_runs_before_the_rebuild():
    assert list(curation_records.kinds()) == ["lifecycle", "supersessions", "syntheses", "conflicts"], (
        "lifecycle first, then supersessions, then syntheses, all before conflicts"
    )
    kind = curation_records.kinds()["supersessions"]

    assert kind.phase == "pre"
    assert kind.domains(_record()) == ["banking.ops"]


def test_apply_supersession_writes_the_record_and_applies_it(monkeypatch, curation_dir):
    from contextlib import contextmanager

    import artmind.temporal as temporal

    session = _Session()

    @contextmanager
    def _fake(*a, **k):
        yield session

    retired = []
    monkeypatch.setattr(temporal, "neo4j_session", _fake)
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda doc_id, *a, **k: retired.append(doc_id))

    temporal.apply_supersession("new-doc", "old-doc", "document", "2026-03-01", detected_by="notice")

    record_id = supersession_records.record_id("new-doc", "old-doc", "document")
    path = curation_dir / "supersessions" / f"{record_id}.json"
    record = json.loads(path.read_text())
    assert (record["effective"], record["detected_by"]) == ("2026-03-01", "notice")
    merges = [p for c, p in session.calls if "MERGE (newer)-[s:SUPERSEDES" in c]
    assert merges[0]["fingerprint"] == curation_records.fingerprint(path.read_bytes())
    assert retired == ["old-doc"]


def test_re_asserting_the_same_supersession_does_not_rewrite_its_file(monkeypatch, curation_dir):
    from contextlib import contextmanager

    import artmind.temporal as temporal

    @contextmanager
    def _fake(*a, **k):
        yield _Session()

    monkeypatch.setattr(temporal, "neo4j_session", _fake)
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda *a, **k: None)
    temporal.apply_supersession("new-doc", "old-doc", "node", None)
    path = curation_dir / "supersessions" / f"{supersession_records.record_id('new-doc', 'old-doc', 'node')}.json"
    before = path.stat().st_mtime_ns

    temporal.apply_supersession("new-doc", "old-doc", "node", None)

    assert path.stat().st_mtime_ns == before


def test_a_node_scope_supersession_does_not_retire_the_older_document(monkeypatch):
    from contextlib import contextmanager

    import artmind.temporal as temporal

    session = _Session()

    @contextmanager
    def _fake(*a, **k):
        yield session

    retired = []
    monkeypatch.setattr(temporal, "neo4j_session", _fake)
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda doc_id, *a, **k: retired.append(doc_id))

    temporal.apply_supersession("new-doc", "old-doc", "node", "2026-03-01")

    assert any("MERGE (newer)-[s:SUPERSEDES" in c for c, _ in session.calls), "the edge is still asserted"
    assert retired == []


class _DatedSession(_Session):
    def __init__(self, valid_from, precision):
        super().__init__()
        self._dated = {"valid_from": valid_from, "precision": precision}

    def run(self, cypher, **params):
        if "d._valid_precision AS precision" in cypher:
            self.calls.append((cypher, params))
            return _Result([self._dated])
        return super().run(cypher, **params)


def test_a_record_with_no_effective_takes_the_newer_documents_stated_date():
    session = _DatedSession("2026-02-01", "day")
    record = supersession_records.record_for(session, "new-doc", "old-doc", "document", None, "manual")
    assert record["effective"] == "2026-02-01"
    (cypher, params), = [(c, p) for c, p in session.calls if "_valid_precision" in c]
    assert params == {"id": "new-doc"} and "(d:Document OR d:DocumentHistory)" in cypher
    assert record["recorded_at"].endswith("+00:00")


def test_a_default_date_is_not_an_effective_date():
    record = supersession_records.record_for(
        _DatedSession("2026-11-17", "none"), "new-doc", "old-doc", "document", None, "manual")
    assert record["effective"] is None


def test_a_given_effective_wins_and_reads_no_date():
    session = _DatedSession("2026-02-01", "day")
    record = supersession_records.record_for(session, "new-doc", "old-doc", "document", "2026-03-01", "manual")
    assert record["effective"] == "2026-03-01"
    assert not [c for c, _ in session.calls if "_valid_precision" in c]


def test_re_asserting_keeps_the_first_recorded_at(curation_dir):
    first = _record()
    first["recorded_at"] = "2026-01-01T00:00:00+00:00"
    curation_records.write_record(supersession_records.NAME, first)
    assert _record()["recorded_at"] == "2026-01-01T00:00:00+00:00"


def test_re_asserting_keeps_the_first_derived_effective(curation_dir):
    first = supersession_records.record_for(
        _DatedSession("2026-02-01", "day"), "new-doc", "old-doc", "document", None, "manual")
    curation_records.write_record(supersession_records.NAME, first)
    path = curation_dir / "supersessions" / f"{first['id']}.json"
    before = path.read_bytes()
    again = supersession_records.record_for(
        _DatedSession("2026-05-09", "day"), "new-doc", "old-doc", "document", None, "manual")
    assert again["effective"] == "2026-02-01"
    curation_records.write_record(supersession_records.NAME, again)
    assert path.read_bytes() == before


def test_apply_supersession_reports_the_records_effective(monkeypatch, curation_dir):
    from contextlib import contextmanager

    import artmind.temporal as temporal

    @contextmanager
    def _fake(*a, **k):
        yield _DatedSession("2026-02-01", "day")

    monkeypatch.setattr(temporal, "neo4j_session", _fake)
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda *a, **k: None)
    assert temporal.apply_supersession("new-doc", "old-doc", "node", None)["effective"] == "2026-02-01"


def test_the_dry_run_effect_reports_the_derived_effective(monkeypatch):
    from contextlib import contextmanager

    import artmind.temporal as temporal

    @contextmanager
    def _fake(*a, **k):
        yield _DatedSession("2026-02-01", "day")

    monkeypatch.setattr("artmind.graph_query.read_session", _fake)
    effect = temporal.supersession_effect("new-doc", "old-doc", "node", None)
    (change,) = [c for c in effect["changes"] if c["kind"] == "document_supersede"]
    assert change["sample"][0]["effective"] == "2026-02-01"
