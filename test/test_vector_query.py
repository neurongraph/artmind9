import pytest

from artmind import vector_query








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








def test_entity_resolve_combines_fulltext_and_vector(monkeypatch):
    """entity_resolve must RRF-combine the fulltext and vector legs."""

    class FakeSession:
        def run(self, cypher, **params):
            if "toLower(e.name)" in cypher:
                return []
            if "entity_name_ft" in cypher:
                return [
                    {
                        "score": 2.1,
                        "entity": {"_id": "ent-1", "name": "Sherlock Holmes"},
                    }
                ]
            return [
                {
                    "score": 0.93,
                    "entity": {"_id": "ent-1", "name": "Sherlock Holmes"},
                },
                {
                    "score": 0.80,
                    "entity": {"_id": "ent-2", "name": "Mycroft Holmes"},
                },
            ]

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(vector_query, "embed_question", lambda question: [0.1, 0.2])
    monkeypatch.setattr(vector_query, "read_session", lambda: FakeSessionContext())

    result = vector_query.entity_resolve("fiction", "the detective Holmes", 5)

    assert result["query_type"] == "entity_resolve"
    assert len(result["rows"]) == 2
    # ent-1 appears in both legs, so it must rank first
    assert result["rows"][0]["entity"]["_id"] == "ent-1"
    # score is the fused RRF score, not a leg's raw (cosine vs BM25) score
    assert result["rows"][0]["matched_by"] == ["vector", "fulltext"]
    assert result["rows"][1]["matched_by"] == ["vector"]
    assert result["rows"][0]["score"] == pytest.approx(2 / 61, abs=1e-6)
    assert result["rows"][0]["score"] > result["rows"][1]["score"]


def test_entity_resolve_reports_aggregation_size():
    """All three legs project the entity's observation and connection counts, so a
    caller can spot a hub before asking for its (large) context."""
    import inspect

    source = "".join(inspect.getsource(f) for f in (
        vector_query.entity_resolve, vector_query._entity_ft_cypher, vector_query._entity_exact_cypher))
    assert source.count("observation_count: ") == 3  # exact, fulltext and vector legs
    assert source.count("connection_count: COUNT {{ (") == 3


def _resolve_with(monkeypatch, *, exact, ft, vec, top_k=5, reference="Sarah"):
    """entity_resolve over a fake session that answers each leg from its own rows."""
    calls = []

    class FakeSession:
        def run(self, cypher, **params):
            calls.append((cypher, params))
            if "toLower(e.name)" in cypher:
                return exact
            if "entity_name_ft" in cypher:
                return ft
            return vec

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(vector_query, "embed_question", lambda question: [0.1])
    monkeypatch.setattr(vector_query, "read_session", lambda: FakeSessionContext())
    return vector_query.entity_resolve("journal", reference, top_k), calls


def _hit(entity_id, name, score=1.0, cls="PERSON"):
    return {"score": score, "entity": {"_id": entity_id, "name": name, "entity_class": cls}}


def test_entity_resolve_returns_the_exact_name_hub_even_when_lucene_ranks_it_below_the_cut(monkeypatch):
    """A person with hundreds of observations scores below short one-off names
    such as 'Tickets for Sarah' in the fulltext leg, and RRF then drops it from
    the top-k. The exact-name leg must still return it, first."""
    noise = [_hit(f"n{i}", f"Tickets for Sarah {i}", 4.0 - i / 10, "OBJECT") for i in range(5)]
    hub = _hit("sarah", "Sarah")

    result, calls = _resolve_with(monkeypatch, exact=[hub], ft=noise, vec=noise)

    assert result["rows"][0]["entity"]["_id"] == "sarah"
    assert result["rows"][0]["matched_by"] == ["exact"]
    assert len(result["rows"]) == 5  # top_k holds: the exact hit takes the first slot
    assert [r["entity"]["_id"] for r in result["rows"][1:]] == ["n0", "n1", "n2", "n3"]
    exact_cypher, exact_params = next(c for c in calls if "toLower(e.name)" in c[0])
    assert exact_params["reference"] == "Sarah"
    assert exact_params["domains"] == ["journal"]


def test_entity_resolve_exact_hit_keeps_its_other_legs_and_is_not_duplicated(monkeypatch):
    hub = _hit("sarah", "Sarah")
    ft = [_hit("sarah", "Sarah", 3.0), _hit("n1", "Tickets for Sarah", 4.0, "OBJECT")]

    result, _ = _resolve_with(monkeypatch, exact=[hub], ft=ft, vec=[_hit("sarah", "Sarah", 0.9)])

    ids = [r["entity"]["_id"] for r in result["rows"]]
    assert ids == ["sarah", "n1"]
    assert result["rows"][0]["matched_by"] == ["exact", "vector", "fulltext"]


def test_entity_resolve_never_cuts_exact_hits_to_top_k(monkeypatch):
    """Same name under two classes (a person and a place): both come back."""
    exact = [_hit("p", "Sarah"), _hit("l", "Sarah", cls="LOCATION")]

    result, _ = _resolve_with(monkeypatch, exact=exact, ft=[], vec=[], top_k=1)

    assert [r["entity"]["_id"] for r in result["rows"]] == ["p", "l"]


def test_entity_resolve_with_no_exact_name_is_unchanged(monkeypatch):
    ft = [_hit("a", "Sarah Jones", 3.0)]

    result, _ = _resolve_with(monkeypatch, exact=[], ft=ft, vec=[])

    assert [r["matched_by"] for r in result["rows"]] == [["fulltext"]]


def test_rrf_combine_without_annotate_keeps_rows_unchanged():
    rows = [{"score": 0.9, "chunk": {"id": "c1"}}]
    assert vector_query._rrf_combine(rows, [], topK=1) == rows


def test_entity_resolve_survives_missing_vector_index(monkeypatch):
    from neo4j.exceptions import ClientError

    class FakeSession:
        def run(self, cypher, **params):
            if "toLower(e.name)" in cypher:
                return []
            if "entity_name_ft" in cypher:
                return [
                    {"score": 1.0, "entity": {"_id": "ent-1", "name": "Sherlock Holmes"}}
                ]
            raise ClientError("IndexNotFound: entity_embedding")

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(vector_query, "embed_question", lambda question: [0.1])
    monkeypatch.setattr(vector_query, "read_session", lambda: FakeSessionContext())

    result = vector_query.entity_resolve("fiction", "Holmes", 5)

    assert len(result["rows"]) == 1
    assert result["rows"][0]["entity"]["_id"] == "ent-1"






def test_entity_name_candidates_runs_only_exact_and_fulltext_without_embedding(monkeypatch):
    calls = []
    exact = [{"entity": {"_id": "a", "name": "Ada", "entity_class": "PERSON"}}]
    ft = [_hit("b", "Ada Lovelace", 3.0), _hit("a", "Ada", 2.0)]

    class FakeSession:
        def run(self, cypher, **params):
            calls.append((cypher, params))
            return exact if "toLower(e.name)" in cypher else ft

    class FakeSessionContext:
        def __enter__(self):
            return FakeSession()

        def __exit__(self, exc_type, exc, tb):
            return False

    def no_embedding(*args, **kwargs):
        raise AssertionError("name-only resolution must not embed")

    monkeypatch.setattr(vector_query, "embed_question", no_embedding)
    monkeypatch.setattr(vector_query.extraction, "embed_text", no_embedding)
    monkeypatch.setattr(vector_query, "read_session", lambda: FakeSessionContext())

    result = vector_query.entity_name_candidates("journal", "Ada", 10)

    assert len(calls) == 2
    assert "toLower(e.name) = toLower($reference)" in calls[0][0]
    assert "entity_name_ft" in calls[1][0]
    assert not any("entity_embedding" in cypher for cypher, _ in calls)
    assert calls[0][1]["reference"] == "Ada" and calls[1][1]["ft_query"]
    assert [r["entity"]["_id"] for r in result["rows"]] == ["a", "b"]
    assert [r["matched_by"] for r in result["rows"]] == [["exact", "fulltext"], ["fulltext"]]
    assert all("score" in r for r in result["rows"])
    assert result["query_type"] == "entity_name_candidates"


from _neo4j_recorder import Recorder

from artmind import graph_query

PASSAGES_VEC = [
    {"row": {"kind": "passage", "id": "c1_p01", "chunk_id": "c1", "doc_id": "d1", "char_start": 0,
             "char_end": 9, "text": "first one", "cosine": 0.82, "embedding": [1.0, 0.0]}},
    {"row": {"kind": "passage", "id": "c2_p01", "chunk_id": "c2", "doc_id": "d2", "char_start": 5,
             "char_end": 20, "text": "second", "cosine": 0.75, "embedding": [0.0, 1.0]}},
]
PASSAGES_FT = [{"row": {"kind": "passage", "id": "c2_p01", "chunk_id": "c2", "doc_id": "d2",
                        "char_start": 5, "char_end": 20, "text": "second"}}]
CHUNKS = [
    {"chunk": {"id": "c1", "name": "C1", "doc_id": "d1", "header_path": "A", "text": "first one ..."},
     "document": {"id": "d1", "name": "Doc 1"}},
    {"chunk": {"id": "c2", "name": "C2", "doc_id": "d2", "header_path": "B", "text": "xxxxx second ..."},
     "document": {"id": "d2", "name": "Doc 2"}},
]


def respond(cypher, params):
    if "VECTOR INDEX passage_embedding" in cypher:
        return PASSAGES_VEC
    if "'passage_text_ft'" in cypher:
        return PASSAGES_FT
    if "UNWIND $chunkIds AS cid" in cypher:
        return [c for c in CHUNKS if c["chunk"]["id"] in params["chunkIds"]]
    return []


def _patch(monkeypatch, session, embed=lambda q: [1.0, 0.0]):
    monkeypatch.setattr(vector_query, "embed_question", embed)
    monkeypatch.setattr(vector_query, "read_session", lambda: session)
    monkeypatch.setattr(graph_query, "expand_domain_family", lambda d: [d, f"{d}.child"])


def test_vector_text_filters_inside_search_with_a_deep_pool(monkeypatch):
    s = Recorder(respond)
    _patch(monkeypatch, s)
    vector_query.vector_text_search(["papers"], "Which method?", 5)
    (vec, vp), = s.queries_containing("VECTOR INDEX passage_embedding")
    assert "WHERE p._domain IN $domains" in vec.split("LIMIT $pool")[0]   # inside SEARCH
    assert vp["domains"] == ["papers", "papers.child"] and vp["pool"] == 50
    (ft, fp), = s.queries_containing("'passage_text_ft'")
    assert "p._domain IN $domains" in ft and fp["pool"] == 50


def test_vector_text_returns_parent_chunks_with_spans_and_fused_scores(monkeypatch):
    s = Recorder(respond)
    _patch(monkeypatch, s)
    out = vector_query.vector_text_search(["papers"], "q", 2, diversity=0.0)
    assert out["parameters"] == {"topK": 2, "pool": 50, "diversity": 0.0, "context": "chunk"}
    assert out["warnings"] == []
    first, second = out["rows"]
    assert first["chunk"]["id"] == "c2"                     # found by both legs
    assert first["matched_by"] == ["vector", "fulltext"] and first["relevance"] == 0.75
    assert first["passage"] == {"id": "c2_p01", "char_start": 5, "char_end": 20}
    assert first["chunk"]["text"] == "xxxxx second ..." and first["document"]["id"] == "d2"
    assert first["source_type"] == "document" and first["passages_matched"] == 1
    assert second["chunk"]["id"] == "c1" and second["matched_by"] == ["vector"]
    assert 0 < second["score"] < first["score"]


def test_context_passage_returns_the_passage_text(monkeypatch):
    s = Recorder(respond)
    _patch(monkeypatch, s)
    out = vector_query.vector_text_search(["papers"], "q", 1, diversity=0.0, context="passage")
    assert out["rows"][0]["chunk"]["text"] == "second"


def test_embedding_down_falls_back_to_keywords_with_a_warning(monkeypatch):
    s = Recorder(respond)

    def down(q):
        raise RuntimeError("ollama unreachable")
    _patch(monkeypatch, s, embed=down)
    out = vector_query.vector_text_search(["papers"], "q", 2)
    assert out["warnings"] and out["warnings"][0].startswith("vector leg unavailable")
    assert [r["chunk"]["id"] for r in out["rows"]] == ["c2"] and out["rows"][0]["relevance"] is None
    assert not s.queries_containing("VECTOR INDEX passage_embedding")


def test_missing_passage_index_names_the_fix(monkeypatch):
    from neo4j.exceptions import ClientError

    def respond_missing(cypher, params):
        if "passage_embedding" in cypher:
            raise ClientError("There is no such vector schema index: passage_embedding")
        return []
    _patch(monkeypatch, Recorder(respond_missing))
    with pytest.raises(ValueError, match="embed-passages"):
        vector_query.vector_text_search(["papers"], "q", 2)


@pytest.mark.parametrize("kwargs", [{"topK": 0}, {"topK": 51}, {"diversity": 1.5}, {"context": "page"}])
def test_bad_arguments_raise(monkeypatch, kwargs):
    _patch(monkeypatch, Recorder(respond))
    args = {"topK": 5, **kwargs}
    top_k = args.pop("topK")
    with pytest.raises(ValueError):
        vector_query.vector_text_search(["papers"], "q", top_k, **args)
