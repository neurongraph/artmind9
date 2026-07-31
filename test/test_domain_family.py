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


def test_normalize_time_loops_every_concrete_child(monkeypatch):
    """A parent-scoped run previously touched only nodes stamped exactly 'banking'
    — normally none, since abstract parents hold no documents.
    """
    import artmind.temporal as t

    monkeypatch.setattr(t, "expand_domain_family", lambda d: ["banking", "banking.policy"])
    seen = []

    def fake_one(domain, dry_run=False):
        seen.append(domain)
        return {"domain": domain, "documents": 1, "entities": 2,
                "deterministic": 3, "llm": 0, "dry_run": dry_run}

    monkeypatch.setattr(t, "_normalize_time_one_domain", fake_one)

    out = t.normalize_time("banking", dry_run=False)

    assert seen == ["banking", "banking.policy"]
    assert out["documents"] == 2
    assert out["entities"] == 4
    assert out["deterministic"] == 6
    assert out["domains_processed"] == ["banking", "banking.policy"]
    assert out["domain"] == "banking"


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
