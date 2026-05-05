from artmind import vector_query


def test_vector_search_shapes_results_and_excludes_embedding(monkeypatch):
    call_count = {"n": 0}

    class FakeSession:
        def run(self, cypher, **params):
            assert params["domain"] == "fiction"
            assert params["topK"] == 2
            call_count["n"] += 1
            if call_count["n"] == 1:  # DocChunk query
                return [
                    {
                        "score": 0.91,
                        "chunk": {
                            "id": "chunk-1",
                            "name": "Chunk 1",
                            "doc_id": "doc-1",
                            "text": "Holmes text",
                            "embedding": [0.1],
                        },
                        "document": {"id": "doc-1", "name": "Story"},
                        "source_type": "document",
                    }
                ]
            else:  # UserChat query
                return []

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(vector_query, "embed_question", lambda question: [0.1, 0.2])
    monkeypatch.setattr(vector_query, "neo4j_session", lambda: FakeSessionContext())

    result = vector_query.vector_search("fiction", "Where?", 2)

    assert result == {
        "domain": "fiction",
        "query_type": "vector",
        "question": "Where?",
        "parameters": {"topK": 2},
        "rows": [
            {
                "score": 0.91,
                "chunk": {
                    "id": "chunk-1",
                    "name": "Chunk 1",
                    "doc_id": "doc-1",
                    "text": "Holmes text",
                },
                "document": {"id": "doc-1", "name": "Story"},
                "source_type": "document",
            }
        ],
    }


def test_vector_search_result_has_source_type_field(monkeypatch):
    class FakeSession:
        def run(self, cypher, **params):
            return []

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(vector_query, "embed_question", lambda question: [0.1, 0.2])
    monkeypatch.setattr(vector_query, "neo4j_session", lambda: FakeSessionContext())

    result = vector_query.vector_search("general", "Alice", 5)

    assert result["domain"] == "general"
    assert result["query_type"] == "vector"
    assert "rows" in result
    assert result["rows"] == []
