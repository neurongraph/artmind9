from artmind import graph_query


def test_chunk_refs_score_is_maxp_over_passages():
    q = graph_query._entity_context_queries()["chunk_refs"]
    assert "OPTIONAL MATCH (p:DocPassage)-[:PART_OF]->(c)" in q
    assert "max(CASE WHEN $queryVector IS NULL OR p.embedding IS NULL THEN null" in q
    assert "c.embedding" not in q


def test_chunk_keyword_uses_passages_best_per_chunk():
    q = graph_query._entity_context_queries()["chunk_keyword"]
    assert "'passage_text_ft'" in q and "node.chunk_id IN $chunkIds" in q
    assert "WITH node.chunk_id AS id, max(score) AS score" in q
