"""Hierarchical domain rollup for the two non-query paths that lacked it."""

import artmind.graph_query as gq


class _Rec:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows


class FakeSession:
    def __init__(self, rows):
        self.runs = []
        self._rows = rows

    def run(self, cypher, **kwargs):
        self.runs.append((cypher, kwargs))
        return _Rec(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_expand_domain_family_includes_parent_and_children(monkeypatch):
    monkeypatch.setattr(
        gq, "read_session",
        lambda: FakeSession([{"dom": "banking.policy"}, {"dom": "banking.cases"}]),
    )

    assert gq.expand_domain_family("banking") == ["banking", "banking.cases", "banking.policy"]


def test_expand_domain_family_avoids_an_unlabelled_full_graph_scan(monkeypatch):
    """MATCH (n) with no label scans every node in the database.

    Document and Entity both carry domain indexes; restricting to them keeps
    the lookup index-backed.
    """
    session = FakeSession([])
    monkeypatch.setattr(gq, "read_session", lambda: session)

    gq.expand_domain_family("banking")

    cypher = session.runs[0][0]
    assert "MATCH (n)" not in cypher
    assert ":Document" in cypher and ":Entity" in cypher


def test_expand_domain_family_leaves_childless_domain_unchanged(monkeypatch):
    monkeypatch.setattr(gq, "read_session", lambda: FakeSession([]))

    assert gq.expand_domain_family("banking.policy") == ["banking.policy"]




def test_detect_conflicts_expands_domains_before_pairing(monkeypatch):
    """--domain banking must mean cross-child conflicts within the family."""
    import artmind.conflicts as c

    monkeypatch.setattr(c, "expand_domain_family", lambda d: {
        "banking": ["banking", "banking.policy", "banking.cases"]
    }[d])
    monkeypatch.setattr(c, "check_refine_precondition", lambda s, d: [])

    seen = {}
    monkeypatch.setattr(
        c, "candidate_pairs",
        lambda domains, nf, st, mp: seen.update({"domains": domains}) or [],
    )

    class _S:
        def run(self, *a, **k):
            class R:
                def single(self_inner):
                    return {"done": []}
            return R()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr(c, "neo4j_session", lambda: _S())

    report = c.detect_conflicts(domains=["banking"], dry_run=True)

    assert seen["domains"] == ["banking", "banking.policy", "banking.cases"]
    assert report["domains_requested"] == ["banking"]


def test_normalize_time_is_gone():
    """Date lifting moved into ingest in Phase 3: the projection's winner rule
    needs each document's valid_from at observation-write time, inside the
    commit transaction, so a backfill command that ran afterwards was both too
    late and (being a swallow-and-warn hook) invisible when it failed."""
    import artmind.temporal as t

    assert not hasattr(t, "normalize_time")
    assert not hasattr(t, "_normalize_time_one_domain")
    assert not hasattr(t, "normalize_ingested_document")
