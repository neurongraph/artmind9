"""The one-off drop of the `:TopicPage` graph copy (spec 2026-11-10 §5.2)."""
from artmind.migrations import drop_topic_page_graph_copy as mig


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def data(self):
        return self._rows

    def single(self):
        return self._rows[0] if self._rows else None


class _Session:
    def __init__(self, indexes, constraint, nodes, edges=0):
        self.indexes, self.constraint, self.nodes, self.edges = list(indexes), constraint, nodes, edges
        self.calls = []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        if cypher.startswith("SHOW INDEXES"):
            return _Result([{"name": n} for n in self.indexes if n in params["names"]])
        if cypher.startswith("SHOW CONSTRAINTS"):
            return _Result([{"name": params["name"]}] if self.constraint else [])
        if "count(DISTINCT t)" in cypher:
            return _Result([{"nodes": self.nodes, "edges": self.edges}])
        if "DETACH DELETE t" in cypher:
            n = min(self.nodes, mig.BATCH)
            self.nodes -= n
            return _Result([{"n": n}])
        return _Result([])


def _writes(s):
    return [c for c, _ in s.calls if not c.startswith("SHOW") and "count(DISTINCT t)" not in c]


def test_nothing_to_drop_reports_none_and_writes_nothing():
    s = _Session([], False, 0)
    assert mig.run(s) == {"status": "none", "report": mig.NONE}
    assert _writes(s) == []


def test_dry_run_counts_and_writes_nothing():
    s = _Session(["topic_embedding", "topic_text_ft"], True, 3, 7)
    out = mig.run(s, dry_run=True)
    assert out["status"] == "would_drop"
    assert (out["nodes"], out["edges"], out["indexes"], out["constraint"]) == (
        3, 7, ["topic_embedding", "topic_text_ft"], "topic_page_slug")
    assert _writes(s) == []


def test_run_deletes_in_batches_then_drops_indexes_and_constraint():
    s = _Session(["topic_text_ft"], True, mig.BATCH + 5)
    out = mig.run(s)
    assert out["status"] == "dropped" and out["deleted"] == mig.BATCH + 5
    writes = _writes(s)
    assert [w for w in writes if "DETACH DELETE t" in w] and all("MATCH (t:TopicPage)" in w for w in writes[:2])
    assert "DROP INDEX topic_text_ft IF EXISTS" in writes
    assert "DROP INDEX topic_embedding IF EXISTS" not in writes
    assert writes[-1] == "DROP CONSTRAINT topic_page_slug IF EXISTS"
