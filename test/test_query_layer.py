"""Query-layer unit tests: asOf resolution, pattern hardening, chunks/entity-context builders (no Neo4j)."""
import re
from datetime import date

import pytest

import artmind.graph_query as gq


# ── resolve_as_of ──────────────────────────────────────────────────────────────

def test_resolve_as_of_none_and_blank():
    assert gq.resolve_as_of(None) is None
    assert gq.resolve_as_of("  ") is None


def test_resolve_as_of_today_and_now_resolve_to_current_date():
    assert gq.resolve_as_of("today") == date.today().isoformat()
    assert gq.resolve_as_of("NOW") == date.today().isoformat()


def test_resolve_as_of_iso_passthrough():
    assert gq.resolve_as_of("2026-07-12") == "2026-07-12"
    assert gq.resolve_as_of("2026-07") == "2026-07"
    assert gq.resolve_as_of("2026") == "2026"


def test_resolve_as_of_rejects_non_iso():
    # The predicate compares strings lexically — an unresolved word like
    # 'tomorrow' would silently hide every node carrying a valid_to.
    with pytest.raises(ValueError):
        gq.resolve_as_of("tomorrow")
    with pytest.raises(ValueError):
        gq.resolve_as_of("12/07/2026")


# ── pattern hardening ──────────────────────────────────────────────────────────

def test_pattern5_all_mode_pins_single_endpoint_pair():
    cypher, _ = gq._pattern_query(
        "pattern5",
        {
            "domains": ["fiction"],
            "entityClass1": "PERSON",
            "entityClass2": "PERSON",
            "entityName1": "Holmes",
            "entityName2": "Watson",
            "mode": "all",
        },
    )
    # All-paths enumeration is exponential per endpoint pair; the pair must be
    # pinned before path expansion. Entity carries `_id` (Phase 4).
    assert re.search(r"ORDER BY e\._id, t\._id\s*\n\s*LIMIT 1", cypher)
    assert cypher.index("LIMIT 1") < cypher.index("[*1..5]")


def test_pattern9_relations_mode_domain_scopes_the_neighbor():
    cypher, _ = gq._pattern_query(
        "pattern9", {"domains": ["fiction"], "entityClass": "PERSON", "topN": 5}
    )
    assert "OPTIONAL MATCH (e)-[r:RELATES_TO]-(t:Entity) WHERE (t._domain IN $domains" in cypher


def test_execute_pattern_patterns_1_through_9_no_longer_apply_asof(monkeypatch):
    # Phase 4: --asOf removed from patterns 1-9's CLI options (the projection
    # is current by construction) — execute_pattern's own signature still
    # accepts it generically (echoed in "parameters" if a caller passes one
    # directly), but the pattern's Cypher no longer references $asOf at all,
    # so it has no effect. Only pattern10 still genuinely applies it now
    # (reaches the History labels), never "ignored" as before.
    captured = {}
    monkeypatch.setattr(gq, "_run_read_query", lambda cypher, params: captured.update(params) or [])
    result = gq.execute_pattern(
        ["fiction"], "pattern1", as_of="today", entityClass="PERSON"
    )
    assert "asOf_ignored" not in result
    assert "asOf" not in captured


def test_execute_pattern_pattern10_resolves_today(monkeypatch):
    captured = {}

    def fake_run(cypher, params):
        captured.update(params)
        return []

    monkeypatch.setattr(gq, "_run_read_query", fake_run)
    result = gq.execute_pattern(["fiction"], "pattern10", as_of="today", documentName="study")
    assert result["parameters"]["asOf"] == date.today().isoformat()
    assert "asOf_ignored" not in result


# ── chunks_by_id ───────────────────────────────────────────────────────────────

def test_chunks_by_id_requires_ids():
    with pytest.raises(ValueError):
        gq.chunks_by_id(["fiction"], [])
    with pytest.raises(ValueError):
        gq.chunks_by_id(["fiction"], ["  "])


def test_chunks_by_id_rejects_negative_expand():
    with pytest.raises(ValueError):
        gq.chunks_by_id(["fiction"], ["doc_001"], expand=-1)


def test_chunks_query_without_expand_has_no_subquery():
    cypher = gq._chunks_query(expand=0, as_of=None)
    assert "CALL (c)" not in cypher
    assert "c.id IN $chunkIds" in cypher
    assert "c._domain IN $domains" in cypher


def test_chunks_query_with_expand_windows_same_document():
    cypher = gq._chunks_query(expand=1, as_of="2026-07-12")
    assert "CALL (c)" in cypher
    assert "MATCH (s:DocChunk {doc_id: c.doc_id})" in cypher
    assert "range(idx - $expand, idx + $expand)" in cypher
    assert "$asOf" in cypher


def test_chunks_by_id_passes_params(monkeypatch):
    captured = {}

    def fake_run(cypher, params):
        captured["cypher"] = cypher
        captured["params"] = params
        return []

    monkeypatch.setattr(gq, "_run_read_query", fake_run)
    result = gq.chunks_by_id(["fiction"], ["c1", "c2"], expand=2, as_of="today")
    assert captured["params"]["chunkIds"] == ["c1", "c2"]
    assert captured["params"]["expand"] == 2
    assert captured["params"]["asOf"] == date.today().isoformat()
    assert result["command"] == "chunks"


# ── entity_context ─────────────────────────────────────────────────────────────

def test_entity_context_requires_entity_id():
    with pytest.raises(ValueError):
        gq.entity_context(["fiction"], "")
    with pytest.raises(ValueError):
        gq.entity_context(["fiction"], "id", include_chunks=-1)
    with pytest.raises(ValueError):
        gq.entity_context(["fiction"], "id", max_connections=-1)
    with pytest.raises(ValueError):
        gq.entity_context(["fiction"], "id", max_list_items=-1)


def test_entity_context_query_shape():
    # No --asOf (Phase 4): the entity and its projected chunks are current by
    # construction.
    queries = gq._entity_context_queries()
    # exact-id anchor (Entity carries `_id`) on every entity-scoped read.
    for name in ("entity", "connections", "chunk_refs", "source_documents"):
        assert "MATCH (e:Entity {_id: $entityId})" in queries[name], name
    # Source chunks via AGGREGATES->Observation->EXTRACTED_FROM (an Entity has
    # never had a direct EXTRACTED_FROM edge), deduplicated; the dead
    # UserChat/:MENTIONS edge (never written) is gone.
    refs = queries["chunk_refs"]
    assert "(e)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(c:DocChunk)" in refs
    assert "WITH DISTINCT c" in refs
    assert ".text" not in refs  # text is fetched only for the chunks that make the cut
    assert "UserChat" not in "".join(queries.values())
    # connections keep direction and drop the per-edge chunk_ids/doc_ids lists
    conns = queries["connections"]
    assert "startNode(r) = e" in conns
    assert "properties(r)" not in conns and "r.chunk_ids" not in conns
    assert "vector.similarity.cosine(t.embedding, $queryVector)" in conns
    assert "vector.similarity.cosine(c.embedding, $queryVector)" in refs
    # the keyword leg is restricted to the entity's own chunks
    assert "WHERE node.id IN $chunkIds" in queries["chunk_keyword"]


class _RecordingSession:
    """Answers each entity_context read by which query it is, and records the
    parameters actually sent -- a bare MagicMock would answer any Cypher."""

    def __init__(self, responses):
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []
        self.queries = gq._entity_context_queries()

    def run(self, cypher, **params):
        name = next(n for n, q in self.queries.items() if q == cypher)
        self.calls.append((name, params))
        response = self.responses.get(name, [])
        return response(params) if callable(response) else response


def _install_session(monkeypatch, session):
    from contextlib import contextmanager

    @contextmanager
    def fake_read_session():
        yield session

    monkeypatch.setattr(gq, "read_session", fake_read_session)


def _hub_responses():
    chunk_text = lambda params: [  # noqa: E731
        {"chunk": {"id": cid, "doc_id": cid.split("_")[0], "text": f"text of {cid}",
                   "document": {"id": cid.split("_")[0], "name": f"{cid.split('_')[0]}.md"}}}
        for cid in params["chunkIds"]
    ]
    return {
        "entity": [{"entityData": {
            "_id": "e1", "name": "Sherlock Holmes", "aliases": ["Holmes"],
            "recent_activity": [f"act{i}" for i in range(15)],
            "_observation_set_hash": "h", "embedding_stale": False, "label": ["Entity", "PERSON"],
        }}],
        "connections": [
            {"rel_type": "SPENDS_TIME_WITH", "direction": "out", "target_id": "p1",
             "target_name": "John Watson", "target_class": "PERSON",
             "observation_count": 16, "doc_count": 16, "score": 0.86},
            {"rel_type": "PARTICIPATES_IN", "direction": "out", "target_id": "ev1",
             "target_name": "Holmes's Birthday Dinner", "target_class": "EVENT",
             "observation_count": 1, "doc_count": 1, "score": 0.80},
            {"rel_type": "IS_AFFECTED_BY", "direction": "out", "target_id": "ev2",
             "target_name": "Holmes's Severe Cold", "target_class": "EVENT",
             "observation_count": 1, "doc_count": 1, "score": 0.88},
            {"rel_type": "SPENDS_TIME_WITH", "direction": "in", "target_id": "p1",
             "target_name": "John Watson", "target_class": "PERSON",
             "observation_count": 15, "doc_count": 15, "score": 0.86},
        ],
        "chunk_refs": [
            {"id": "d1_000", "name": "c", "doc_id": "d1", "current": True, "doc_valid_from": None, "score": 0.70},
            {"id": "d2_000", "name": "c", "doc_id": "d2", "current": True, "doc_valid_from": None, "score": 0.90},
            {"id": "d3_000", "name": "c", "doc_id": "d3", "current": True, "doc_valid_from": None, "score": 0.60},
            {"id": "d0_000", "name": "c", "doc_id": "d0", "current": False, "doc_valid_from": None, "score": 0.99},
        ],
        "chunk_keyword": [{"id": "d3_000", "score": 4.2}],
        "chunk_text": chunk_text,
        "source_documents": [
            {"document": {"id": d, "name": f"{d}.md", "valid_from": None}} for d in ("d0", "d1", "d2", "d3")
        ],
    }


def test_entity_context_without_query_ranks_by_evidence_and_bounds_output(monkeypatch):
    session = _RecordingSession(_hub_responses())
    _install_session(monkeypatch, session)

    result = gq.entity_context(["fiction"], " e1 ", include_chunks=2, max_connections=2, max_list_items=10)

    # parameters actually sent: trimmed id, no query vector, no keyword leg
    sent = dict(session.calls)
    assert sent["entity"]["entityId"] == "e1"
    assert sent["connections"]["queryVector"] is None
    assert "chunk_keyword" not in sent
    # chunk text fetched only for the chunks returned, current-first
    assert sent["chunk_text"]["chunkIds"] == ["d3_000", "d2_000"]

    row = result["rows"][0]
    entity = row["entityData"]
    assert "_observation_set_hash" not in entity and "embedding_stale" not in entity
    # lists keep their newest (tail) items; the cut is reported
    assert entity["recent_activity"] == [f"act{i}" for i in range(5, 15)]
    assert row["truncated_properties"] == {"recent_activity": 15}
    # the summary counts every edge, by type and direction
    assert row["connections_total"] == 4
    assert {"rel_type": "SPENDS_TIME_WITH", "direction": "out", "count": 1} in row["connection_summary"]
    # unranked: strongest evidence first, capped, compact (no chunk_ids/doc_ids)
    assert [c["target"]["id"] for c in row["connections"]] == ["p1", "p1"]
    assert [c["direction"] for c in row["connections"]] == ["out", "in"]
    assert set(row["connections"][0]) == {"rel_type", "direction", "target", "observation_count", "doc_count"}
    # the retired chunk (current=False) sorts last despite its score
    assert row["chunks_total"] == 4
    assert [c["id"] for c in row["chunks"]] == ["d3_000", "d2_000"]
    assert [c["id"] for c in row["more_chunks"]] == ["d1_000", "d0_000"]
    # returned chunks' documents lead the Sources candidates
    assert [d["id"] for d in row["source_documents"]][:2] == ["d3", "d2"]
    assert row["source_documents_total"] == 4
    assert result["parameters"] == {
        "entityId": "e1", "includeChunks": 2, "maxConnections": 2, "maxListItems": 10,
    }


def test_entity_context_query_ranks_by_relevance(monkeypatch):
    session = _RecordingSession(_hub_responses())
    _install_session(monkeypatch, session)
    monkeypatch.setattr("artmind.vector_query.embed_question", lambda q: [0.1, 0.2])

    result = gq.entity_context(
        ["fiction"], "e1", include_chunks=2, query="When is Holmes's birthday?"
    )

    sent = dict(session.calls)
    assert sent["connections"]["queryVector"] == [0.1, 0.2]
    # the anchor's own name is no topic word: only "birthday" reaches Lucene
    assert sent["chunk_keyword"]["ftQuery"] == "birthday"
    assert sent["chunk_keyword"]["chunkIds"] == ["d1_000", "d2_000", "d3_000", "d0_000"]

    row = result["rows"][0]
    # the topic-word match beats a higher embedding score and more evidence
    assert row["connections"][0]["target"]["id"] == "ev1"
    assert row["connections"][1]["target"]["id"] == "ev2"
    # RRF: d2 (vector #1) and d3 (keyword #1, vector #3) lead; d0 is retired
    assert [c["id"] for c in row["chunks"]] == ["d3_000", "d2_000"]
    assert result["parameters"]["query"] == "When is Holmes's birthday?"


def test_entity_context_rel_type_filters_connections_not_summary(monkeypatch):
    _install_session(monkeypatch, _RecordingSession(_hub_responses()))

    result = gq.entity_context(["fiction"], "e1", rel_types=["participates_in"])

    row = result["rows"][0]
    assert [c["rel_type"] for c in row["connections"]] == ["PARTICIPATES_IN"]
    assert row["connections_matched"] == 1
    assert row["connections_total"] == 4
    assert len(row["connection_summary"]) == 4  # SPENDS_TIME_WITH counted per direction
    assert result["parameters"]["relType"] == ["participates_in"]


def test_entity_context_unknown_entity_returns_no_rows(monkeypatch):
    session = _RecordingSession({"entity": []})
    _install_session(monkeypatch, session)

    result = gq.entity_context(["fiction"], "missing")

    assert result["rows"] == []
    assert [name for name, _ in session.calls] == ["entity"]


def test_query_terms_drop_stopwords_and_anchor_names():
    assert gq._query_terms("When is Holmes's birthday?", ["Sherlock Holmes"]) == ["birthday"]
    assert gq._query_terms("What gifts did she receive") == ["gifts", "receive"]
    # plural/possessive folding when matching
    assert gq._term_hits("Birthday Gifts", ["birthdays", "gift"]) == 2


def test_fuse_ranks_rewards_agreement():
    fused = gq._fuse_ranks(["a", "b", "c"], ["c"])
    assert max(fused, key=fused.get) == "c"


def test_order_source_documents_keeps_returned_chunk_docs():
    docs = [{"id": f"d{i}", "valid_from": None} for i in range(5)]
    ordered = gq._order_source_documents(docs, ["d1", "d0", "d1"], cap=3)
    assert [d["id"] for d in ordered] == ["d1", "d0", "d4"]
    # a cap below the returned chunks' documents never drops them
    assert len(gq._order_source_documents(docs, ["d1", "d0", "d2"], cap=1)) == 3


def test_entity_context_cli_passes_new_options(monkeypatch):
    from click.testing import CliRunner

    from artmind.cli import cli

    captured = {}

    def fake_entity_context(domains, entity_id, **kwargs):
        captured.update(kwargs, domains=domains, entity_id=entity_id)
        return {"rows": []}

    monkeypatch.setattr(gq, "entity_context", fake_entity_context)
    result = CliRunner().invoke(cli, [
        "query", "entity-context", "--domain", "fiction", "--entityId", "e1",
        "--query", "birthday?", "--relType", "PARTICIPATES_IN,GIFTS", "--relType", "VISITS",
        "--maxConnections", "7", "--maxListItems", "3", "--compact",
    ])
    assert result.exit_code == 0, result.output
    assert captured["query"] == "birthday?"
    assert captured["rel_types"] == ["PARTICIPATES_IN", "GIFTS", "VISITS"]
    assert captured["max_connections"] == 7
    assert captured["max_list_items"] == 3
