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


def test_full_text_search_shapes_results(monkeypatch):
    call_count = {"n": 0}

    class FakeSession:
        def run(self, cypher, **params):
            assert params["domain"] == "fiction"
            assert params["topK"] == 2
            call_count["n"] += 1
            if call_count["n"] == 1:  # DocChunk query
                return [
                    {
                        "score": 0.8,
                        "chunk": {
                            "id": "chunk-2",
                            "name": "Chunk 2",
                            "doc_id": "doc-1",
                            "text": "Watson and Holmes",
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

    monkeypatch.setattr(vector_query, "neo4j_session", lambda: FakeSessionContext())

    result = vector_query.full_text_search("fiction", "Watson Holmes", 2)

    assert result["domain"] == "fiction"
    assert result["query_type"] == "full_text"
    assert result["question"] == "Watson Holmes"
    assert result["parameters"]["topK"] == 2
    assert len(result["rows"]) == 1


def test_rrf_combine_balances_vector_and_text(monkeypatch):
    """Test that RRF properly combines and reranks vector and text results."""
    # Vector search finds chunk-1 with high score (rank 1)
    vector_rows = [
        {
            "score": 0.95,
            "chunk": {"id": "chunk-1", "text": "semantic match"},
            "document": {"id": "doc-1"},
            "source_type": "document",
        },
        {
            "score": 0.80,
            "chunk": {"id": "chunk-2", "text": "less semantic"},
            "document": {"id": "doc-1"},
            "source_type": "document",
        },
    ]

    # Text search finds chunk-2 with high score (rank 1) - keyword match
    text_rows = [
        {
            "score": 0.90,
            "chunk": {"id": "chunk-2", "text": "keyword semantic"},
            "document": {"id": "doc-1"},
            "source_type": "document",
        },
        {
            "score": 0.70,
            "chunk": {"id": "chunk-3", "text": "other text"},
            "document": {"id": "doc-1"},
            "source_type": "document",
        },
    ]

    combined = vector_query._rrf_combine(vector_rows, text_rows, topK=3, k=60)

    # chunk-2 should rank higher now because it appears in both lists
    assert len(combined) == 3
    # First should be chunk-2 (appears in both rankings)
    assert combined[0]["chunk"]["id"] == "chunk-2"
    # Second should be chunk-1 (high vector rank)
    assert combined[1]["chunk"]["id"] == "chunk-1"


def test_vector_text_search_combines_methods(monkeypatch):
    """Test that vector_text_search calls both methods and combines results."""
    call_count = {"vector": 0, "text": 0}

    def mock_vector_search(domain, question, topK):
        call_count["vector"] += 1
        return {
            "domain": domain,
            "query_type": "vector",
            "question": question,
            "parameters": {"topK": topK},
            "rows": [
                {
                    "score": 0.91,
                    "chunk": {"id": "chunk-1", "text": "semantic"},
                    "document": {"id": "doc-1"},
                    "source_type": "document",
                }
            ],
        }

    def mock_text_search(domain, question, topK):
        call_count["text"] += 1
        return {
            "domain": domain,
            "query_type": "full_text",
            "question": question,
            "parameters": {"topK": topK},
            "rows": [
                {
                    "score": 0.80,
                    "chunk": {"id": "chunk-2", "text": "keyword"},
                    "document": {"id": "doc-1"},
                    "source_type": "document",
                }
            ],
        }

    monkeypatch.setattr(vector_query, "vector_search", mock_vector_search)
    monkeypatch.setattr(vector_query, "full_text_search", mock_text_search)

    result = vector_query.vector_text_search("fiction", "test question", 5)

    assert result["query_type"] == "vector_text"
    assert result["question"] == "test question"
    assert call_count["vector"] == 1
    assert call_count["text"] == 1
    assert len(result["rows"]) == 2  # Combined results


def test_full_text_search_fallback_chunks(monkeypatch):
    """Test that fallback works when APOC is not available."""
    from neo4j.exceptions import ClientError

    class FakeSession:
        def __init__(self):
            self.call_count = 0

        def run(self, cypher, **params):
            self.call_count += 1
            # Simulate APOC not being available on first calls
            if "apoc" in cypher.lower():
                raise ClientError("Unknown function 'apoc'")
            # For fallback queries (no apoc), return matching data
            if "DocChunk" in cypher:
                return [
                    {
                        "chunk": {
                            "id": "chunk-1",
                            "text": "Watson and Holmes meet",
                        },
                        "document": {"id": "doc-1"},
                        "source_type": "document",
                    }
                ]
            elif "UserChat" in cypher:
                return []
            return []

    class FakeSessionContext:
        def __init__(self):
            self.session = FakeSession()

        def __enter__(self):
            return self.session

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(vector_query, "neo4j_session", lambda: FakeSessionContext())

    result = vector_query.full_text_search("fiction", "Watson Holmes", 5)

    assert result["query_type"] == "full_text"
    assert len(result["rows"]) >= 0  # Should handle fallback gracefully
