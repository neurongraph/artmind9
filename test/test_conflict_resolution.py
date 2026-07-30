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
    session = FakeSession()
    monkeypatch.setattr(c, "neo4j_session", lambda: session)

    out = c.resolve_conflict("abc123", "resolved", reason="Policy v3 settled it")

    assert out["id"] == "abc123"
    assert out["status"] == "resolved"
    cypher, kwargs = session.runs[0]
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
