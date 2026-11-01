# test/test_embed_sweep_summaries.py
from _neo4j_recorder import Recorder

from artmind import embed_sweep


def _session(summary_rows):
    served = {"done": False}

    def respond(cypher, params):
        if "MATCH (s:DocSummary)" in cypher and "RETURN s.id AS id" in cypher:
            if served["done"]:
                return []
            served["done"] = True
            return summary_rows
        return []
    return Recorder(respond=respond)


def test_summaries_without_a_current_vector_are_embedded_with_the_document_prefix():
    s = _session([{"id": "d1:summary", "text": "Whole thing."}])
    out = embed_sweep.reconcile_passages(s, embed=lambda text: [0.5, 0.5], embed_key="K", cache=None)
    assert out["summaries_embedded"] == 1
    sets = s.queries_containing("MATCH (s:DocSummary {id: $id}) SET s.embedding = $embedding")
    assert sets and sets[0][1] == {"id": "d1:summary", "embedding": [0.5, 0.5], "embed_key": "K"}
    query = s.queries_containing("RETURN s.id AS id")[0]
    assert "s.embedding IS NULL OR s._embed_key IS NULL OR s._embed_key <> $embed_key" in query[0]
    assert query[1]["embed_key"] == "K"


def test_a_failed_summary_embedding_is_skipped_not_looped():
    s = _session([{"id": "d1:summary", "text": "x"}])

    def boom(text):
        raise RuntimeError("down")

    out = embed_sweep.reconcile_passages(s, embed=boom, embed_key="K", cache=None)
    assert out["summaries_embedded"] == 0 and out["summaries_remaining"] == 1


def test_doc_and_domain_scopes_reach_the_summary_query():
    s = _session([])
    embed_sweep.reconcile_passages(s, embed=lambda t: [1.0], embed_key="K", cache=None, doc_ids=["d1"], domain="notes")
    cypher, params = s.queries_containing("RETURN s.id AS id")[0]
    assert "s.doc_id IN $doc_ids" in cypher and "s._domain = $domain" in cypher
    assert params["doc_ids"] == ["d1"] and params["domain"] == "notes"


def test_a_chunk_scope_reaches_chunk_and_document_summaries():
    s = _session([])
    embed_sweep.reconcile_passages(s, embed=lambda t: [1.0], embed_key="K", cache=None, chunk_ids=["c1"])
    cypher, params = s.queries_containing("RETURN s.id AS id")[0]
    assert "s.chunk_id IN $chunk_ids" in cypher
    assert "EXISTS { MATCH (c:DocChunk) WHERE c.id IN $chunk_ids AND c.doc_id = s.doc_id }" in cypher
    # a pattern comprehension needs a relationship pattern; a lone node is a SyntaxError in Neo4j
    assert "[(c:DocChunk)" not in cypher
    assert params["chunk_ids"] == ["c1"]
