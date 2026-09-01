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


# ── summary["unembedded_chunk_ids"]: what _commit_document_tx surfaces ──────


def _stage_with_chunks(tmp_path, chunks):
    doc_kg_dir = _stage(tmp_path)
    (doc_kg_dir / "chunks.json").write_text(json.dumps(chunks), encoding="utf-8")
    return doc_kg_dir


def test_only_the_still_unembedded_chunks_are_surfaced(tmp_path, monkeypatch):
    """One chunk already has a vector (e.g. merged in from the sidecar by
    `_load_staged`), one doesn't -- only the second's id should end up in
    `summary["unembedded_chunk_ids"]`."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    doc_kg_dir = _stage_with_chunks(tmp_path, [
        {"id": "d1_001", "doc_id": "phys-1", "text": "has a vector", "embedding": [0.1, 0.2]},
        {"id": "d1_002", "doc_id": "phys-1", "text": "no vector yet"},
    ])

    summary = ing._write_to_neo4j(doc_kg_dir, "banking")

    assert summary["unembedded_chunk_ids"] == ["d1_002"]


def test_no_unembedded_chunks_is_an_empty_list_not_missing(tmp_path, monkeypatch):
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    doc_kg_dir = _stage_with_chunks(tmp_path, [
        {"id": "d1_001", "doc_id": "phys-1", "text": "has a vector", "embedding": [0.1]},
    ])

    summary = ing._write_to_neo4j(doc_kg_dir, "banking")

    assert summary["unembedded_chunk_ids"] == []


def test_commit_to_graph_sweeps_exactly_the_chunks_the_tx_surfaced(tmp_path, monkeypatch):
    """End-to-end (no mocking of `_write_to_neo4j` this time): a document with
    one embedded and one unembedded chunk, committed via `commit_to_graph`,
    must sweep only the unembedded one's id."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    doc_kg_dir = _stage_with_chunks(tmp_path, [
        {"id": "d1_001", "doc_id": "phys-1", "text": "has a vector", "embedding": [0.1]},
        {"id": "d1_002", "doc_id": "phys-1", "text": "no vector yet"},
    ])
    swept_chunks: list = []
    monkeypatch.setattr(
        ing, "_sweep_chunk_embeddings", lambda chunk_ids: swept_chunks.append(chunk_ids) or 0
    )

    assert ing.commit_to_graph(doc_kg_dir, "banking") is True
    assert swept_chunks == [["d1_002"]]


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
    monkeypatch.setattr(
        ing, "_sweep_chunk_embeddings", lambda chunk_ids: order.append("chunk_sweep") or 0
    )

    assert ing.commit_to_graph(_stage(tmp_path), "banking") is True
    assert order == ["commit", "sweep", "chunk_sweep"]


def test_the_chunk_sweep_is_scoped_to_this_commits_unembedded_chunk_ids(tmp_path, monkeypatch):
    """Mirrors the entity sweep's `affected_keys` scoping — the chunk sweep
    must receive exactly `summary["unembedded_chunk_ids"]`, not a full-graph
    scan and not the entity keys."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    monkeypatch.setattr(
        ing, "_write_to_neo4j",
        lambda d, domain=None, defer_rebuild=False: {
            "affected_keys": [("n", "C", "d")],
            "unembedded_chunk_ids": ["d1_002"],
        },
    )
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: 0)
    swept_chunks: list = []
    monkeypatch.setattr(
        ing, "_sweep_chunk_embeddings", lambda chunk_ids: swept_chunks.append(chunk_ids) or 0
    )

    assert ing.commit_to_graph(_stage(tmp_path), "banking") is True
    assert swept_chunks == [["d1_002"]]


def test_a_failing_chunk_sweep_does_not_fail_an_already_committed_write(tmp_path, monkeypatch):
    """Same contract as the entity sweep: the commit already succeeded and the
    graph is correct. A chunk that could not be embedded stays NULL and is
    picked up by the next sweep. Exercises the real `_sweep_chunk_embeddings`
    (not a mock of it) so its own internal try/except is what's under test —
    mirrors `test_a_failing_embed_sweep_does_not_fail_an_already_committed_write`,
    which patches the underlying `embed_missing_entity_embeddings`, not the
    `_sweep_embeddings` wrapper around it."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    monkeypatch.setattr(
        ing, "_write_to_neo4j",
        lambda d, domain=None, defer_rebuild=False: {
            "affected_keys": [], "unembedded_chunk_ids": ["d1_001"],
        },
    )
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: 0)

    import artmind.embed_sweep as embed_sweep

    def boom(session, **kw):
        raise RuntimeError("ollama is down")

    monkeypatch.setattr(embed_sweep, "embed_missing_chunk_embeddings", boom)

    assert ing.commit_to_graph(_stage(tmp_path), "banking") is True


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
    swept_chunks: list = []
    monkeypatch.setattr(
        ing, "_sweep_chunk_embeddings", lambda chunk_ids: swept_chunks.append(chunk_ids) or 0
    )

    import artmind.projection as projection
    rebuilds: list = []
    monkeypatch.setattr(projection, "rebuild", lambda tx, keys, **kw: rebuilds.append(keys) or {})

    assert ing.commit_to_graph(_stage(tmp_path), "banking", defer_rebuild=True) is True
    assert rebuilds == [], "the rebuild is deferred to the end of the batch"
    assert swept == [], "so is the sweep — there is nothing fresh to embed yet"
    assert swept_chunks == [], "the chunk sweep is gated the same way as the entity sweep"


# ── _sweep_chunk_embeddings: two scoping modes ───────────────────────────────
#
# Closes the batch-ingest gap: `commit_to_graph`'s chunk sweep only fires on
# the non-deferred path (scoped to `chunk_ids`); a multi-file batch defers
# every per-document commit and relies on `rebuild_projection`'s own
# domain-scoped sweep instead (below).


def test_sweep_chunk_embeddings_short_circuits_with_neither_scope(monkeypatch):
    """Nothing to scope to — a real no-op, must not even open a session."""
    import artmind.graph_query as gq

    def boom(*a, **k):
        raise AssertionError("must not open a session when neither chunk_ids nor domain is given")

    monkeypatch.setattr(gq, "neo4j_session", boom)

    assert ing._sweep_chunk_embeddings() == 0
    assert ing._sweep_chunk_embeddings(chunk_ids=[]) == 0
    assert ing._sweep_chunk_embeddings(chunk_ids=None, domain=None) == 0


def test_sweep_chunk_embeddings_domain_scope_passes_through(monkeypatch):
    """`domain="general"` reaches `embed_missing_chunk_embeddings` as the
    `domain` kwarg, with `chunk_ids` left `None` -- not silently dropped or
    conflated with the id-scoping path."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)

    import artmind.embed_sweep as embed_sweep

    calls: list = []

    def fake(session, **kw):
        calls.append(kw)
        return {"embedded": 3}

    monkeypatch.setattr(embed_sweep, "embed_missing_chunk_embeddings", fake)

    result = ing._sweep_chunk_embeddings(domain="general")

    assert result == 3
    assert calls == [{"chunk_ids": None, "domain": "general"}]


# ── rebuild_projection: the domain-scoped chunk sweep ────────────────────────


def test_rebuild_projection_sweeps_chunks_by_domain_when_domain_given(monkeypatch):
    session = _RetractSession()
    _patch_session(monkeypatch, session)

    import artmind.projection as projection

    monkeypatch.setattr(projection, "full_rebuild", lambda tx, domains=None, **kw: {})
    monkeypatch.setattr(projection, "all_keys", lambda tx, domains=None: set())
    monkeypatch.setattr(ing, "_sweep_embeddings", lambda domain, keys: 0)
    swept_chunks: list = []
    monkeypatch.setattr(
        ing, "_sweep_chunk_embeddings", lambda **kw: swept_chunks.append(kw) or 7
    )

    summary = ing.rebuild_projection("general")

    assert swept_chunks == [{"domain": "general"}]
    assert summary["chunks_embedded"] == 7, (
        "a dropped or mis-assigned summary['chunks_embedded'] = ... line must fail this test"
    )


def test_rebuild_projection_skips_the_chunk_sweep_on_a_global_rebuild(monkeypatch):
    """No `domain` — a multi-domain/global rebuild -- must skip the chunk
    sweep entirely, mirroring how the entity sweep is already skipped in
    that case."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)

    import artmind.projection as projection

    monkeypatch.setattr(projection, "full_rebuild", lambda tx, domains=None, **kw: {})
    monkeypatch.setattr(projection, "all_keys", lambda tx, domains=None: set())
    swept_chunks: list = []
    monkeypatch.setattr(
        ing, "_sweep_chunk_embeddings", lambda **kw: swept_chunks.append(kw) or 0
    )

    ing.rebuild_projection()

    assert swept_chunks == [], "a global rebuild (domain=None) must not sweep chunks either"


# ── a missing embedding must write NO "embedding" key, never "embedding": [] ─
#
# `_flatten_props` drops v == [] for every property; before this fix
# `_commit_document_tx` special-cased "embedding" out of `_flatten_props`'s
# input and always re-added it, defaulting to []. That meant a chunk with no
# vector got `embedding: []` written -- a present, non-null value. In real
# Neo4j, `[] IS NULL` is false, so `embed_missing_chunk_embeddings`'s
# `WHERE c.embedding IS NULL` sweep query would never find it (a mocked
# session can't catch this -- see CLAUDE.md's `update confirm` postmortem).
# Worse: `_merge_relabeled` writes DocChunk with `SET n += $props`
# (additive) when `replace=False`, so an always-present "embedding" key could
# silently overwrite a real vector a prior sweep had already filled in.


def _docchunk_merge_calls(session):
    """Every `_merge_relabeled` call this commit made against :DocChunk,
    as (cypher, kwargs) — the same query string carries both the CREATE and
    the SET-existing branches, so this substring identifies it uniquely
    (mirrors test_filing_metadata.py's `_RecordingSession` pattern)."""
    return [(c, kw) for c, kw in session.calls if "CREATE (n:DocChunk" in c]


def test_a_chunk_with_no_embedding_sends_no_embedding_key(tmp_path, monkeypatch):
    """Regression test for the sweep-never-matches bug: the props dict sent
    for a chunk with no vector must not contain "embedding" at all -- not
    "embedding": []."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    doc_kg_dir = _stage_with_chunks(tmp_path, [
        {"id": "d1_001", "doc_id": "phys-1", "text": "no vector yet"},
    ])

    ing._write_to_neo4j(doc_kg_dir, "banking")

    calls = _docchunk_merge_calls(session)
    assert calls, "expected a DocChunk MERGE"
    _, kwargs = calls[0]
    assert "embedding" not in kwargs["props"], (
        f"a chunk with no vector must omit the 'embedding' key entirely, "
        f"got props={kwargs['props']!r}"
    )


def test_a_chunk_with_a_real_embedding_still_writes_it_unchanged(tmp_path, monkeypatch):
    """The other half of the fix: a genuine vector must still flow through
    byte-for-byte -- `_neo4j_value` passes a list of plain floats through
    unchanged, so folding "embedding" into `_flatten_props` must not touch it."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)
    vector = [0.1, 0.2, 0.3]
    doc_kg_dir = _stage_with_chunks(tmp_path, [
        {"id": "d1_001", "doc_id": "phys-1", "text": "has a vector", "embedding": vector},
    ])

    ing._write_to_neo4j(doc_kg_dir, "banking")

    calls = _docchunk_merge_calls(session)
    assert calls, "expected a DocChunk MERGE"
    _, kwargs = calls[0]
    assert kwargs["props"]["embedding"] == vector


def test_a_re_commit_with_no_incoming_vector_cannot_clobber_a_prior_one(tmp_path, monkeypatch):
    """Data-loss regression test: commit the same chunk id twice, first with a
    real embedding, then (e.g. a metadata-only reingest with no local sidecar)
    with none. `_merge_relabeled`'s DocChunk write is additive
    (`SET n += $props`), so the only way the second commit cannot clobber the
    first commit's vector is if its outgoing props dict has no "embedding" key
    at all -- proven directly here, which is sufficient per CLAUDE.md's own
    guidance (a mocked/recording session can't enforce real Neo4j `+=`
    semantics, only what was actually sent)."""
    session = _RetractSession()
    _patch_session(monkeypatch, session)
    monkeypatch.setattr(ing, "_ensure_neo4j_schema", lambda *a, **k: None)

    vector = [0.4, 0.5, 0.6]
    doc_kg_dir = _stage_with_chunks(tmp_path, [
        {"id": "d1_001", "doc_id": "phys-1", "text": "has a vector", "embedding": vector},
    ])
    ing._write_to_neo4j(doc_kg_dir, "banking")
    first_calls = _docchunk_merge_calls(session)
    assert first_calls[-1][1]["props"]["embedding"] == vector

    # Re-stage the identical chunk id, this time with no embedding at all
    # (e.g. the sidecar was absent for this reingest run).
    doc_kg_dir = _stage_with_chunks(tmp_path, [
        {"id": "d1_001", "doc_id": "phys-1", "text": "has a vector"},
    ])
    ing._write_to_neo4j(doc_kg_dir, "banking")
    second_calls = _docchunk_merge_calls(session)

    _, second_props = second_calls[-1]
    assert "embedding" not in second_props["props"], (
        "a re-commit with no incoming vector must send no 'embedding' key at "
        "all -- SET n += $props only touches keys present in props, so this "
        "is what prevents it from clobbering the vector the first commit wrote"
    )
