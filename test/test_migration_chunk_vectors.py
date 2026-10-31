from _neo4j_recorder import Recorder

from artmind.migrations import chunk_vectors_to_passages as m


def session(indexes=(), lacking=(), vectors=(0,)):
    left = list(vectors)

    def respond(cypher, params):
        if "SHOW INDEXES" in cypher:
            return [{"name": n} for n in indexes]
        if "NOT EXISTS { MATCH (:DocPassage)-[:PART_OF]->(c) }" in cypher:
            return [{"domain": d, "n": n} for d, n in lacking]
        if "c.embedding IS NOT NULL" in cypher and "REMOVE c.embedding" in cypher:
            return [{"n": left.pop(0) if left else 0}]
        if "c.embedding IS NOT NULL" in cypher:
            return [{"n": sum(left)}]
        return []
    return Recorder(respond)


def test_nothing_left_reports_none():
    s = session()
    out = m.run(s)
    assert out["status"] == "none" and out["report"] == "legacy chunk vectors: none"
    assert not s.queries_containing("DROP INDEX")


def test_waits_while_chunks_lack_passages():
    s = session(indexes=("chunk_embedding",), lacking=[("papers", 3)])
    out = m.run(s)
    assert out["status"] == "waiting" and out["domains_without_passages"] == {"papers": 3}
    assert "artmind ingest embed-passages" in out["report"]
    assert not s.queries_containing("DROP INDEX") and not s.queries_containing("REMOVE c.embedding")


def test_cleans_up_when_every_chunk_has_passages():
    s = session(indexes=("chunk_embedding", "chunk_text_ft"), vectors=(1000, 7))
    out = m.run(s)
    drops = [c for c, _ in s.queries_containing("DROP INDEX")]
    assert drops == ["DROP INDEX chunk_embedding IF EXISTS", "DROP INDEX chunk_text_ft IF EXISTS"]
    (lack, _), = s.queries_containing("NOT EXISTS { MATCH (:DocPassage)-[:PART_OF]->(c) }")
    assert "trim(coalesce(c.text, '')) <> ''" in lack
    removes = s.queries_containing("REMOVE c.embedding")
    assert "(c:DocChunk OR c:DocChunkHistory)" in removes[0][0]
    assert out == {"status": "cleaned", "dropped_indexes": ["chunk_embedding", "chunk_text_ft"],
                   "vectors_removed": 1007, "report": "legacy chunk vectors: none"}
