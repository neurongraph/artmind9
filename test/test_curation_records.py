"""Curation records -- graph-only writes made to travel as one vault file per
record (spec 2026-09-26 §7, §14 A6) -- and the first kind, conflicts.

No Neo4j: a recording transaction stands in, and every graph test asserts on
the Cypher and the parameters actually sent (CLAUDE.md §3)."""
import hashlib
import json
from contextlib import contextmanager

import pytest

from artmind import conflict_records, curation_records


@pytest.fixture(autouse=True)
def curation_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / ".artmind" / "data" / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def data(self):
        return list(self._rows)


class _Tx:
    def __init__(self, answers=None):
        self.calls = []
        self._answers = answers or {}

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        for marker, rows in self._answers.items():
            if marker in cypher:
                return _Result(rows)
        return _Result()


def _record(**overrides):
    record = conflict_records.record_from_detection(
        "c0ffee",
        {
            "id_a": "ea", "name_a": "Fee reversal", "domain_a": "banking.ops", "key_a": "fee reversal|POLICY|banking.ops",
            "id_b": "eb", "name_b": "Fee reversals", "domain_b": "banking.risk", "key_b": "fee reversals|POLICY|banking.risk",
            "entity_class": "POLICY",
        },
        {"verdict": "conflicting_claims", "aspect": "approval limit", "claim_a": "CEO", "claim_b": "Manager", "severity": "high"},
        [{"id": "d1_001", "doc_id": "d1", "text": "..."}],
        [{"id": "d2_004", "doc_id": "d2", "text": "..."}],
        "model-x",
        "2026-09-28T10:00:00+00:00",
    )
    record.update(overrides)
    return record


# ── the file ────────────────────────────────────────────────────────────────


def test_a_record_serialises_deterministically():
    data = curation_records.serialize({"b": 1, "a": {"y": 2, "x": 3}})

    assert data == b'{\n  "a": {\n    "x": 3,\n    "y": 2\n  },\n  "b": 1\n}\n'
    assert curation_records.fingerprint(data) == hashlib.sha256(data).hexdigest()


def test_write_record_writes_one_file_per_record_and_returns_its_fingerprint(curation_dir):
    record = _record()

    fp = curation_records.write_record("conflicts", record)

    path = curation_dir / "conflicts" / "c0ffee.json"
    assert path.read_bytes() == curation_records.serialize(record)
    assert fp == curation_records.fingerprint(path.read_bytes())
    assert [p.name for p in (curation_dir / "conflicts").iterdir()] == ["c0ffee.json"], "no temp file left"
    assert curation_records.read_record("conflicts", "c0ffee") == record


def test_an_unchanged_record_is_not_rewritten(curation_dir):
    record = _record()
    curation_records.write_record("conflicts", record)
    path = curation_dir / "conflicts" / "c0ffee.json"
    before = path.stat().st_ino, path.stat().st_mtime_ns

    curation_records.write_record("conflicts", dict(record))

    assert (path.stat().st_ino, path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("bad", ["../escape", "a/b", ".hidden", "", "x y"])
def test_an_unsafe_record_id_is_refused(bad):
    with pytest.raises(ValueError, match="safe curation record id"):
        curation_records.record_path("conflicts", bad)


def test_delete_record_removes_the_file(curation_dir):
    curation_records.write_record("conflicts", _record())

    assert curation_records.delete_record("conflicts", "c0ffee") is True
    assert curation_records.read_record("conflicts", "c0ffee") is None
    assert curation_records.delete_record("conflicts", "c0ffee") is False


def test_parse_tolerates_garbage():
    assert curation_records.parse(None) is None
    assert curation_records.parse(b"not json") is None
    assert curation_records.parse(b"[1, 2]") is None
    assert curation_records.parse(b'{"id": "x"}') == {"id": "x"}


def test_the_conflicts_kind_is_registered_to_run_after_the_rebuild():
    kind = curation_records.kinds()["conflicts"]

    assert kind.phase == "post", "CONFLICTS_WITH joins two rebuilt :Entity nodes"
    assert kind.domains(_record()) == ["banking.ops", "banking.risk"]


# ── the conflict record ─────────────────────────────────────────────────────


def test_a_detected_conflict_becomes_an_open_record_with_both_sides():
    record = _record()

    assert record["status"] == "open"
    assert record["entities"] == [
        {"side": "a", "id": "ea", "key": "fee reversal|POLICY|banking.ops", "name": "Fee reversal", "domain": "banking.ops"},
        {"side": "b", "id": "eb", "key": "fee reversals|POLICY|banking.risk", "name": "Fee reversals", "domain": "banking.risk"},
    ]
    assert record["evidence"] == [
        {"side": "a", "chunk_id": "d1_001", "doc_id": "d1"},
        {"side": "b", "chunk_id": "d2_004", "doc_id": "d2"},
    ]
    assert (record["aspect"], record["claim_a"], record["claim_b"], record["severity"]) == (
        "approval limit", "CEO", "Manager", "high",
    )
    json.dumps(record)  # plain JSON, nothing a file cannot hold


def test_apply_sets_every_field_from_the_record_and_joins_both_entities():
    tx = _Tx()
    record = _record(status="resolved", resolution_reason="v3 settled it", resolved_at="2026-09-28T11:00:00+00:00")

    keys = conflict_records.apply(tx, record, "fp-1")

    assert keys == set()
    (cypher, params), (ev_cypher, ev_params) = tx.calls
    assert cypher.strip().startswith("MERGE (co:Conflict {id: $id})")
    assert "ON CREATE" not in cypher, "the record is the truth: every field is SET"
    assert "MERGE (a)-[ra:CONFLICTS_WITH]->(b)" in cypher and "MERGE (co)-[:CONFLICT_OF]->(a)" in cypher
    assert params == {
        "id": "c0ffee", "verdict": "conflicting_claims", "aspect": "approval limit",
        "claim_a": "CEO", "claim_b": "Manager", "severity": "high", "entity_class": "POLICY",
        "domains": ["banking.ops", "banking.risk"], "status": "resolved",
        "resolution_reason": "v3 settled it", "resolved_at": "2026-09-28T11:00:00+00:00",
        "detected_at": "2026-09-28T10:00:00+00:00", "detected_by_model": "model-x",
        "fingerprint": "fp-1", "idA": "ea", "idB": "eb",
    }
    assert "MERGE (co)-[:EVIDENCE {side: ev.side}]->(c)" in ev_cypher
    assert ev_params == {"id": "c0ffee", "evidence": [
        {"side": "a", "chunk_id": "d1_001"}, {"side": "b", "chunk_id": "d2_004"},
    ]}


def test_apply_without_evidence_sends_no_evidence_statement():
    tx = _Tx()

    conflict_records.apply(tx, _record(evidence=[]), "fp")

    assert len(tx.calls) == 1


def test_remove_deletes_the_edges_and_only_an_adjudicator_node():
    tx = _Tx()

    conflict_records.remove(tx, {"id": "c0ffee"})

    assert tx.calls == [
        ("MATCH (:Entity)-[r:CONFLICTS_WITH {conflict_id: $id}]->(:Entity) DELETE r", {"id": "c0ffee"}),
        ("MATCH (co:Conflict {id: $id, _source: 'adjudicator'}) DETACH DELETE co", {"id": "c0ffee"}),
    ]


def test_read_fingerprints_is_one_query(monkeypatch):
    import artmind.graph_query as graph_query

    session = _Tx(answers={"record_fingerprint": [{"id": "a", "fingerprint": "f"}, {"id": "b", "fingerprint": None}]})
    timeouts = []

    @contextmanager
    def _fake(access_mode=None, *, timeout=None):
        timeouts.append(timeout)
        yield session

    monkeypatch.setattr(graph_query, "neo4j_session", _fake)

    assert conflict_records.read_fingerprints(["b", "a", "a"], timeout=2.0) == {"a": "f", "b": None}
    assert session.calls[0][1] == {"ids": ["a", "b"]}
    assert timeouts == [2.0]
    assert conflict_records.read_fingerprints([]) == {}
    assert len(session.calls) == 1


def test_record_from_graph_rebuilds_a_pre_file_conflicts_record():
    tx = _Tx(answers={"properties(co) AS co": [{
        "co": {"id": "c0ffee", "aspect": "limit", "claim_a": "A", "claim_b": "B", "severity": "high",
               "status": "resolved", "resolution_reason": "done", "resolved_at": "t1",
               "detected_at": "t0", "detected_by_model": "m", "domains": ["x", "y"], "_source": "adjudicator"},
        "entities": [
            {"id": "eb", "key": "b|C|y", "name": "B", "domain": "y"},
            {"id": "ea", "key": "a|C|x", "name": "A", "domain": "x"},
        ],
        "evidence": [{"side": "b", "chunk_id": "c2", "doc_id": "d2"}, {"side": None, "chunk_id": None, "doc_id": None}],
    }]})

    record = conflict_records.record_from_graph(tx, "c0ffee")

    assert record["status"] == "resolved" and record["resolution_reason"] == "done"
    assert [e["id"] for e in record["entities"]] == ["ea", "eb"]
    assert [e["side"] for e in record["entities"]] == ["a", "b"]
    assert record["evidence"] == [{"side": "b", "chunk_id": "c2", "doc_id": "d2"}]
    assert record["domains"] == ["x", "y"]
    assert tx.calls[0][1] == {"id": "c0ffee"}


def test_record_from_graph_is_none_without_a_node():
    assert conflict_records.record_from_graph(_Tx(), "nope") is None
