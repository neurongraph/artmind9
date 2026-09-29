"""Conflict status transitions — the missing half of query graph conflicts --status."""

from click.testing import CliRunner

import artmind.conflicts as c


class _Rec:
    def __init__(self, data):
        self._data = data

    def single(self):
        return self._data


class FakeSession:
    def __init__(self, found=True):
        self.runs = []
        self._found = found

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        return _Rec({"id": "abc123", "status": kwargs.get("status")} if self._found else None)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_resolve_conflict_sets_status_and_provenance(monkeypatch):
    """A conflict with no record (a projection conflict: derived, rebuilt with
    the projection) keeps its graph-only status write."""
    session = FakeSession()
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

    out = c.resolve_conflict("abc123", "resolved", reason="Policy v3 settled it")

    assert out["id"] == "abc123"
    assert out["status"] == "resolved"
    cypher, kwargs = next((cy, kw) for cy, kw in session.runs if "co.status = $status" in cy)
    assert "co.status = $status" in cypher
    assert "co.resolved_at" in cypher
    assert kwargs["status"] == "resolved"
    assert kwargs["reason"] == "Policy v3 settled it"


def test_resolve_conflict_raises_on_unknown_id(monkeypatch):
    """A no-match must fail loudly: silently succeeding would let an operator
    believe they closed a conflict that is still open. Orphaned CONFLICTS_WITH
    edges have no Conflict node and so cannot carry status at all.
    """
    import pytest

    session = FakeSession(found=False)
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

    with pytest.raises(ValueError, match="abc123"):
        c.resolve_conflict("abc123", "dismissed")


def test_resolve_conflict_rejects_unknown_status(monkeypatch):
    import pytest

    monkeypatch.setattr(c, "neo4j_session", lambda: FakeSession())

    with pytest.raises(ValueError, match="status"):
        c.resolve_conflict("abc123", "banana")


def test_resolve_conflict_cli_reports_unknown_id(monkeypatch):
    import artmind.cli as cli

    monkeypatch.setattr(c, "neo4j_session", lambda: FakeSession(found=False))

    result = CliRunner().invoke(cli.cli, ["ingest", "resolve-conflict", "abc123", "--status", "resolved"])

    assert result.exit_code != 0
    assert "abc123" in result.output
    assert "No Conflict node" in result.output


# ── an adjudicator conflict's resolution travels as its record (spec §14 A6) ──


import json

import pytest

from artmind import conflict_records, curation_records


@pytest.fixture()
def curation_dir(tmp_path, monkeypatch):
    import paths

    target = tmp_path / "curation"
    monkeypatch.setattr(paths, "CURATION_DIR", target)
    return target


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def single(self):
        return self._rows[0] if self._rows else None

    def data(self):
        return self._rows


class RecordingSession(FakeSession):
    """Answers `record_from_graph`'s read with `graph_node` (None: no node)."""

    def __init__(self, graph_node=None):
        super().__init__()
        self._graph_node = graph_node

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        if "properties(co) AS co" in cypher:
            return _Rows([self._graph_node] if self._graph_node else [])
        if "MATCH (a:Entity {_id: $idA}" in cypher:
            return _Rows([{"n": 1}])   # both entities are in the graph
        if "DocChunk" in cypher:
            return _Rows([{"n": len({e["chunk_id"] for e in kwargs["evidence"]})}])
        return _Rows([])


def _stored(curation_dir, **overrides):
    record = {
        "id": "abc123", "verdict": "conflicting_claims", "aspect": "limit", "claim_a": "A", "claim_b": "B",
        "severity": "high", "entity_class": "POLICY",
        "entities": [{"side": "a", "id": "ea", "key": "a|POLICY|x", "name": "A", "domain": "x"},
                     {"side": "b", "id": "eb", "key": "b|POLICY|y", "name": "B", "domain": "y"}],
        "domains": ["x", "y"], "evidence": [], "status": "open", "resolution_reason": None,
        "resolved_at": None, "detected_at": "t0", "detected_by_model": "m", "source": "adjudicator",
    }
    record.update(overrides)
    curation_records.write_record("conflicts", record)
    return record


def _applied(session):
    return [kw for cy, kw in session.runs if cy.strip().startswith("MERGE (co:Conflict {id: $id})")]


def _fingerprinted(session):
    return [kw for cy, kw in session.runs if "co.record_fingerprint = $fingerprint" in cy]


def _edged(session):
    return [kw for cy, kw in session.runs if "MATCH (a:Entity {_id: $idA}" in cy]


def test_resolving_a_recorded_conflict_updates_its_file_and_applies_it(monkeypatch, curation_dir):
    _stored(curation_dir)
    session = RecordingSession()
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

    out = c.resolve_conflict("abc123", "dismissed", reason="not a real clash")

    record = json.loads((curation_dir / "conflicts" / "abc123.json").read_text())
    assert (record["status"], record["resolution_reason"]) == ("dismissed", "not a real clash")
    assert record["resolved_at"]
    applied = _applied(session)
    assert len(applied) == 1
    assert (applied[0]["status"], applied[0]["resolution_reason"]) == ("dismissed", "not a real clash")
    assert _fingerprinted(session) == [{
        "id": "abc123",
        "fingerprint": curation_records.fingerprint((curation_dir / "conflicts" / "abc123.json").read_bytes()),
    }]
    assert not [cy for cy, _ in session.runs if "properties(co) AS co" in cy], "the file is read, not the graph"
    assert out == {"id": "abc123", "status": "dismissed", "reason": "not a real clash"}


def test_resolving_a_recorded_conflict_this_graph_has_not_seen_yet_still_applies(monkeypatch, curation_dir):
    """The record arrived with a pull and `vault sync` has not run: the file
    is the truth, so resolving writes it and MERGEs the node."""
    _stored(curation_dir)
    session = RecordingSession(graph_node=None)
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

    c.resolve_conflict("abc123", "resolved")

    assert _applied(session)[0]["status"] == "resolved"


def test_resolving_a_conflict_detected_before_records_writes_its_first_record(monkeypatch, curation_dir):
    session = RecordingSession(graph_node={
        "co": {"id": "abc123", "aspect": "limit", "claim_a": "A", "claim_b": "B", "severity": "high",
               "status": "open", "detected_at": "t0", "detected_by_model": "m", "domains": ["x", "y"],
               "_source": "adjudicator"},
        "entities": [{"id": "ea", "key": "a|POLICY|x", "name": "A", "domain": "x"},
                     {"id": "eb", "key": "b|POLICY|y", "name": "B", "domain": "y"}],
        "evidence": [],
    })
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

    c.resolve_conflict("abc123", "resolved", reason="ok")

    record = json.loads((curation_dir / "conflicts" / "abc123.json").read_text())
    assert record["status"] == "resolved"
    assert [e["id"] for e in record["entities"]] == ["ea", "eb"]
    assert (_edged(session)[0]["idA"], _edged(session)[0]["idB"]) == ("ea", "eb")
