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
    # pinned before path expansion.
    assert re.search(r"ORDER BY e\.id, t\.id\s*\n\s*LIMIT 1", cypher)
    assert cypher.index("LIMIT 1") < cypher.index("[*1..5]")


def test_pattern9_relations_mode_domain_scopes_the_neighbor():
    cypher, _ = gq._pattern_query(
        "pattern9", {"domains": ["fiction"], "entityClass": "PERSON", "topN": 5}
    )
    assert "OPTIONAL MATCH (e)-[r]-(t:Entity) WHERE (t.domain IN $domains" in cypher


def test_execute_pattern_flags_ignored_asof(monkeypatch):
    monkeypatch.setattr(gq, "_run_read_query", lambda cypher, params: [])
    result = gq.execute_pattern(
        ["fiction"], "pattern10", as_of="today", documentName="study"
    )
    assert result["asOf_ignored"] is True
    # pattern1 honours asOf, so no flag there
    result = gq.execute_pattern(
        ["fiction"], "pattern1", as_of="today", entityClass="PERSON"
    )
    assert "asOf_ignored" not in result


def test_execute_pattern_resolves_today(monkeypatch):
    captured = {}

    def fake_run(cypher, params):
        captured.update(params)
        return []

    monkeypatch.setattr(gq, "_run_read_query", fake_run)
    gq.execute_pattern(["fiction"], "pattern1", as_of="today", entityClass="PERSON")
    assert captured["asOf"] == date.today().isoformat()


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
    assert "c.domain IN $domains" in cypher


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


def test_entity_context_query_shape():
    cypher = gq._entity_context_query(as_of="2026-07-12")
    # exact-id anchor, structural edges, currency-first chunk ordering,
    # text capped to $includeChunks with the remainder as ids only
    assert "MATCH (e:Entity {id: $entityId})" in cypher
    assert "(e)-[:EXTRACTED_FROM]->(c:DocChunk)" in cypher
    assert "(chat:UserChat)-[:MENTIONS]->(e)" in cypher
    assert "ORDER BY c.valid_to IS NULL DESC, c.id" in cypher
    assert "allChunks[0..$includeChunks] AS chunks" in cypher
    assert "allChunks[$includeChunks..]" in cypher
    # asOf applies to the entity and its chunks
    assert cypher.count("$asOf") >= 2


def test_entity_context_passes_params(monkeypatch):
    captured = {}

    def fake_run(cypher, params):
        captured.update(params)
        return []

    monkeypatch.setattr(gq, "_run_read_query", fake_run)
    result = gq.entity_context(["fiction"], " e42 ", include_chunks=3)
    assert captured["entityId"] == "e42"
    assert captured["includeChunks"] == 3
    assert result["command"] == "entity_context"
