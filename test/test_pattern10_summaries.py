# test/test_pattern10_summaries.py
from artmind import graph_query


def test_pattern10_adds_one_summary_per_matched_document(monkeypatch):
    calls = []

    def run(cypher, params):
        calls.append((cypher, params))
        if "DocSummary" in cypher:
            return [{"doc_id": "d1", "name": "a.md", "summary": "The whole of a."}]
        return [{"document": {"id": "d1", "name": "a.md"}, "chunk": {"id": "c1", "text": "x"}},
                {"document": {"id": "d1", "name": "a.md"}, "chunk": {"id": "c2", "text": "y"}},
                {"document": {"id": "d2", "name": "ab.md"}, "chunk": {"id": "c3", "text": "z"}}]

    monkeypatch.setattr(graph_query, "_run_read_query", run)
    out = graph_query.execute_pattern(["notes"], "pattern10", documentName="a")
    assert out["summaries"] == [{"doc_id": "d1", "name": "a.md", "summary": "The whole of a."},
                                {"doc_id": "d2", "name": "ab.md", "summary": None}]
    cypher, params = calls[-1]
    assert params == {"docIds": ["d1", "d2"]}
    assert "MATCH (s:DocSummary {id: did + ':summary'})" in cypher
