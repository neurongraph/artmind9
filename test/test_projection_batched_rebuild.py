"""The batched projection rebuild (plan 2026-10-03, B1).

Root cause, found live on 2026-10-03: job 5f5c2d11's deferred full rebuild ran
4,332 keys in ONE transaction, which ran colima's 2 GB VM out of memory and
killed Neo4j. Every assertion here is on the transactions opened and the
queries sent, in order -- a bare mock would report success for any of them.
"""
from __future__ import annotations

import pytest

import artmind.graph_query as graph_query
import artmind.projection as projection
import artmind.same_as as same_as


class _Rows:
    def __init__(self, rows):
        self._rows = list(rows)

    def data(self):
        return list(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None

    def consume(self):
        return None


class _Recorder:
    """One Neo4j session, recording every transaction it opens, every query
    it runs and every `projection.rebuild` batch, in order. `all_keys`'
    Observation query answers with `self.keys`; every other query is empty."""

    def __init__(self):
        self.keys: list[tuple[str, str, str]] = []
        self.log: list[tuple] = []

    def run(self, cypher, **params):
        self.log.append(("run", cypher, params))
        if "RETURN DISTINCT n.key" in cypher and "(n:Observation)" in cypher:
            return _Rows([{"key": "|".join(k)} for k in self.keys])
        return _Rows([])

    def execute_read(self, fn, *args, **kwargs):
        self.log.append(("read",))
        return fn(self, *args, **kwargs)

    def execute_write(self, fn, *args, **kwargs):
        self.log.append(("write",))
        return fn(self, *args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def events(self) -> list:
        """The log reduced to what the assertions care about."""
        out: list = []
        for entry in self.log:
            if entry[0] in ("read", "write"):
                out.append(entry[0])
            elif entry[0] == "rebuild":
                out.append(("rebuild", len(entry[1])))
            elif entry[0] == "run" and "MERGE (s:ProjectionState" in entry[1]:
                out.append("record")
        return out

    def batches(self) -> list[list[tuple]]:
        return [entry[1] for entry in self.log if entry[0] == "rebuild"]


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(graph_query, "neo4j_session", lambda *a, **k: rec)
    monkeypatch.setattr(same_as, "load_groups", lambda: [])
    monkeypatch.setattr(same_as, "content_hash", lambda path=None: "same-as-hash")
    monkeypatch.setattr(projection, "schema_set_hash", lambda domains_dir=None: "schema-hash")

    def fake_rebuild(tx, keys, **kwargs):
        rec.log.append(("rebuild", list(keys), kwargs))
        return {"rebuilt": len(keys), "deleted": 0, "absent": 0, "keys": len(keys)}

    monkeypatch.setattr(projection, "rebuild", fake_rebuild)
    monkeypatch.setattr(projection, "REBUILD_BATCH", 3)
    return rec


def _keys(n: int, domain: str = "general") -> list[tuple[str, str, str]]:
    return [(f"k{i}", "PERSON", domain) for i in range(n)]


def test_rebuild_in_batches_commits_one_write_transaction_per_batch(recorder):
    totals = projection.rebuild_in_batches(_keys(7, "d"))

    assert recorder.events() == ["write", ("rebuild", 3), "write", ("rebuild", 3), "write", ("rebuild", 1)]
    assert (totals["rebuilt"], totals["keys"], totals["batches"]) == (7, 7, 3)
    for entry in recorder.log:
        if entry[0] == "rebuild":
            assert entry[2]["same_as_groups"] == []
            assert callable(entry[2]["synthesis_loader"]), "a synthesis must survive the rebuild"


def test_rebuild_in_batches_names_the_batch_that_failed(recorder, monkeypatch):
    calls: list[list] = []

    def boom(tx, keys, **kwargs):
        calls.append(list(keys))
        if len(calls) == 2:
            raise RuntimeError("MemoryPoolOutOfMemoryError")
        return {"rebuilt": len(keys), "keys": len(keys)}

    monkeypatch.setattr(projection, "rebuild", boom)

    with pytest.raises(RuntimeError, match=r"batch 2/3 \(MemoryPoolOutOfMemoryError\)"):
        projection.rebuild_in_batches(_keys(7, "d"))
    assert len(calls) == 2, "no batch may run after the one that failed"


def test_table2graph_keeps_its_names_for_the_moved_helpers():
    import artmind.table2graph as t2g

    assert t2g.batch_keys is projection.batch_keys
    assert t2g.REBUILD_BATCH == projection.REBUILD_BATCH == 400
