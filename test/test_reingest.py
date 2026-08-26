"""Re-ingest of a known identity: the prior version's assertions leave
`latest`, and the projection — not a heuristic — decides what disappears.

Re-ingesting is always a replace (`--replace` is gone, Phase 2). What changed
in Phase 3 is *how*: the old code hard-retracted a document's contributions and
ran a scoped entity GC of its own. Now the transition is assertion-time only.
What changed AGAIN in Phase 4: the transition is a **label swap**
(:Observation -> :ObservationHistory, :DocChunk -> :DocChunkHistory), not a
`_status` property set — there is no `_status` property left on these nodes.
Chunks used to be hard `DETACH DELETE`d here; they are relabelled instead, and
edge provenance is no longer retracted here at all — `RELATES_TO` aggregate
edges are entirely derived from `ASSERTS_RELATION` observation edges by the
projection rebuild, which recomputes them from the affected keys this
transition already hands it. Which entities should vanish is decided by the
projection's zero-latest-observations rule over those keys.

We assert the params actually sent and which Cypher ran — never summary counts
— because a MagicMock session returns truthy for any query (CLAUDE.md).
"""
import json

import pytest

import artmind.ingest as ing


# ── fake session plumbing ────────────────────────────────────────────────────


def _classify(cypher: str) -> str:
    if "REMOVE o:Observation SET o:ObservationHistory" in cypher:
        return "demote_observations"
    if "REMOVE c:DocChunk SET c:DocChunkHistory" in cypher:
        return "relabel_chunks"
    if "DETACH DELETE c" in cypher:
        return "chunk_delete"
    if "DETACH DELETE e" in cypher:
        return "entity_delete"
    return "other"


class _Rec:
    def __init__(self, session, cypher, kw):
        self.session, self.cypher, self.kw = session, cypher, kw

    def single(self):
        return {"n": self.session.counts.get(_classify(self.cypher), 0), "c": 0}

    def data(self):
        return []

    def consume(self):
        return None


class _RetractSession:
    def __init__(self, counts=None):
        self.counts = counts or {}
        self.calls: list[tuple[str, dict]] = []

    def run(self, cypher, **kw):
        self.calls.append((cypher, kw))
        return _Rec(self, cypher, kw)

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    execute_read = execute_write

    def call(self, kind):
        for cypher, kw in self.calls:
            if _classify(cypher) == kind:
                return cypher, kw
        raise AssertionError(f"no {kind!r} query ran; saw {[_classify(c) for c, _ in self.calls]}")

    def ran(self, kind) -> bool:
        return any(_classify(c) == kind for c, _ in self.calls)


def _patch_session(monkeypatch, session):
    class _Ctx:
        def __enter__(self):
            return session

        def __exit__(self, *exc):
            return False

    import artmind.graph_query as gq

    monkeypatch.setattr(gq, "neo4j_session", lambda *a, **k: _Ctx())
    return session


# ── the version/label-swap transition ───────────────────────────────────────


def test_the_prior_versions_observations_are_demoted_not_deleted():
    """Assertion time, not valid time: a relabelled observation keeps the
    valid-time window it always had and stays in storage."""
    session = _RetractSession(counts={"demote_observations": 4})

    result = ing._retract_prior_version(session, "banking", "docA")

    cypher, kw = session.call("demote_observations")
    assert kw["doc_id"] == "docA"
    assert "DELETE" not in cypher, "observations are immutable — never deleted here"
    assert result["observations_demoted"] == 4


def test_the_transition_never_deletes_entities_itself():
    """The zero-observations GC rule owns entity deletion now. Three competing
    GC mechanisms is exactly how 235 entities stayed live."""
    session = _RetractSession()

    ing._retract_prior_version(session, "banking", "docA")

    assert not session.ran("entity_delete"), "entity GC belongs to the projection rebuild"


def test_edge_provenance_is_no_longer_retracted_here():
    """RELATES_TO is entirely derived from ASSERTS_RELATION observation edges
    now (Phase 4) — relabelling this document's observations already makes
    their ASSERTS_RELATION edges structurally invisible to the next rebuild's
    aggregation query, so there is nothing left for this transition itself to
    retract."""
    session = _RetractSession()

    ing._retract_prior_version(session, "banking", "docA")

    for cypher, _ in session.calls:
        assert "doc_ids" not in cypher
        assert "chunk_ids" not in cypher
        assert "RELATES_TO" not in cypher


def test_the_documents_chunks_are_relabelled_not_deleted():
    session = _RetractSession(counts={"relabel_chunks": 3})

    result = ing._retract_prior_version(session, "banking", "docA")

    cypher, kw = session.call("relabel_chunks")
    assert kw["doc_id"] == "docA"
    assert "DELETE" not in cypher
    assert result["chunks"] == 3
    assert not session.ran("chunk_delete"), "chunks are relabelled, never hard-deleted"


# ── commit ordering, and the removal of the swallow ─────────────────────────


def _stage(tmp_path, **extra):
    doc_kg_dir = tmp_path / "doc"
    doc_kg_dir.mkdir(exist_ok=True)
    document = {"id": "phys-1", "name": "docA.md", "domain": "banking"}
    document.update(extra)
    (doc_kg_dir / "document.json").write_text(json.dumps(document), encoding="utf-8")
    for name in ("chunks.json", "observations.json", "relationships.json"):
        (doc_kg_dir / name).write_text("[]", encoding="utf-8")
    return doc_kg_dir


def test_the_prior_version_is_demoted_before_the_new_observations_land(tmp_path, monkeypatch):
    """Otherwise a re-commit would accrete onto the version it replaces."""
    order: list[str] = []
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    monkeypatch.setattr(
        ing, "_retract_prior_version",
        lambda tx, domain, doc_id: order.append(f"retract:{domain}:{doc_id}") or {},
    )
    monkeypatch.setattr(
        ing, "_write_observations",
        lambda tx, observations, doc_id: order.append("observations") or 0,
    )

    ing._write_to_neo4j(_stage(tmp_path), "banking")

    assert order == ["retract:banking:phys-1", "observations"]


def test_a_failed_projection_rebuild_FAILS_the_commit(tmp_path, monkeypatch):
    """The behaviour Phase 3 deliberately inverts.

    The pre-redesign hooks caught their own exceptions and logged a warning, so
    a broken projection was indistinguishable from a healthy one. A silently
    skipped projection is a silently stale query layer.
    """
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)

    import artmind.projection as projection

    def boom(tx, keys, **kw):
        raise RuntimeError("projection rebuild failed")

    monkeypatch.setattr(projection, "rebuild", boom)

    assert ing.commit_to_graph(_stage(tmp_path), "banking") is False


def test_a_failed_retraction_FAILS_the_commit(tmp_path, monkeypatch):
    """Also inverted. Retraction is part of the transaction now, not a
    best-effort prelude to it."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)

    def boom(tx, domain, doc_id):
        raise RuntimeError("retraction blew up")

    monkeypatch.setattr(ing, "_retract_prior_version", boom)

    assert ing.commit_to_graph(_stage(tmp_path), "banking") is False


def test_the_embed_sweep_runs_after_the_commit_not_inside_it(tmp_path, monkeypatch):
    """It calls the embedding service, which a transaction cannot do."""
    order: list[str] = []
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    monkeypatch.setattr(
        ing, "_write_to_neo4j",
        lambda d, domain=None, defer_rebuild=False: (
            order.append("commit"), {"affected_keys": [("n", "C", "d")]}
        )[1],
    )
    monkeypatch.setattr(
        ing, "_sweep_embeddings", lambda domain, keys: order.append("sweep") or 0
    )

    assert ing.commit_to_graph(_stage(tmp_path), "banking") is True
    assert order == ["commit", "sweep"]


def test_a_failing_embed_sweep_does_not_fail_an_already_committed_write(tmp_path, monkeypatch):
    """The opposite of the rebuild: the commit already succeeded and the graph
    is correct. An entity that could not be embedded keeps `embedding_stale`
    and is picked up next sweep — which is why the flag exists."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    monkeypatch.setattr(
        ing, "_write_to_neo4j",
        lambda d, domain=None, defer_rebuild=False: {"affected_keys": [("n", "C", "d")]},
    )

    def boom(session, domain, embed_model, keys=None):
        raise RuntimeError("ollama is down")

    monkeypatch.setattr(ing, "embed_missing_entity_embeddings", boom)

    assert ing.commit_to_graph(_stage(tmp_path), "banking") is True


def test_a_deferred_commit_skips_both_the_rebuild_and_the_sweep(tmp_path, monkeypatch):
    """Directory ingest: one full rebuild at the end, not N incremental ones."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    swept: list = []
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: swept.append(keys) or 0)

    import artmind.projection as projection
    rebuilds: list = []
    monkeypatch.setattr(projection, "rebuild", lambda tx, keys, **kw: rebuilds.append(keys) or {})

    assert ing.commit_to_graph(_stage(tmp_path), "banking", defer_rebuild=True) is True
    assert rebuilds == [], "the rebuild is deferred to the end of the batch"
    assert swept == [], "so is the sweep — there is nothing fresh to embed yet"
