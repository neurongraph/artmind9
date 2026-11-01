import pytest
from neo4j.exceptions import ClientError

from _neo4j_recorder import Recorder

from artmind import graph_query, survey, vector_query


def respond(cypher, params):
    if "passage_embedding" in cypher:
        return [{"row": {"kind": "passage", "id": "a1_p01", "chunk_id": "a1", "doc_id": "A", "text": "alpha", "cosine": 0.9}},
                {"row": {"kind": "passage", "id": "b1_p01", "chunk_id": "b1", "doc_id": "B", "text": "beta", "cosine": 0.8}}]
    if "passage_text_ft" in cypher:
        return [{"row": {"kind": "passage", "id": "b1_p01", "chunk_id": "b1", "doc_id": "B", "text": "beta"}}]
    if "summary_embedding" in cypher:
        if params["level"] == "document":
            return [{"row": {"kind": "summary", "id": "C:summary", "doc_id": "C", "chunk_id": None,
                             "text": "gamma doc", "cosine": 0.7}}]
        return [{"row": {"kind": "summary", "id": "b1:summary", "doc_id": "B", "chunk_id": "b1",
                         "text": "beta part", "cosine": 0.6}}]
    if "UNWIND $docIds" in cypher:
        return [{"doc_id": d, "title": f"Doc {d}", "name": f"{d}.md", "domain": "notes",
                 "summary": None if d == "A" else f"sum {d}"} for d in params["docIds"]]
    if "count(d) AS total" in cypher:
        return [{"domain": "notes", "total": 3, "summarised": 2}]
    return []


def _patch(monkeypatch, session, embed=lambda q: [1.0, 0.0]):
    monkeypatch.setattr(vector_query, "embed_question", embed)
    monkeypatch.setattr(survey, "read_session", lambda: session)
    monkeypatch.setattr(graph_query, "expand_domain_family", lambda d: [d])


def test_three_legs_rank_documents(monkeypatch):
    _patch(monkeypatch, Recorder(respond))
    out = survey.survey_search(["notes"], "what do the notes say about beta", top_docs=10)
    assert [d["doc_id"] for d in out["documents"]] == ["B", "C", "A"]
    b = out["documents"][0]
    assert b["ranks"] == {"P": 1, "C": 1}
    assert b["hits"] == {"passages": 1, "chunk_summaries": 1}
    assert b["chunk_summary"] == {"chunk_id": "b1", "text": "beta part"}
    assert b["passages"] == [{"id": "b1_p01", "chunk_id": "b1", "text": "beta", "relevance": 0.8}]
    assert b["title"] == "Doc B" and b["summary"] == "sum B"
    assert out["documents"][2]["summary"] is None
    assert out["query_type"] == "survey" and out["topics"] == []
    assert out["parameters"] == {"topDocs": 10, "passages": 1, "pool": 300, "legs": ["D", "C", "P"]}


def test_summary_legs_filter_inside_the_index_by_domain_and_level(monkeypatch):
    s = Recorder(respond)
    _patch(monkeypatch, s)
    survey.survey_search(["notes"], "q")
    calls = s.queries_containing("VECTOR INDEX summary_embedding")
    assert {p["level"] for _, p in calls} == {"document", "chunk"}
    cypher, params = calls[0]
    assert "WHERE s._domain IN $domains AND s.level = $level" in cypher
    assert params["domains"] == ["notes"] and params["pool"] == 300


def test_missing_summaries_are_warned_per_domain(monkeypatch):
    _patch(monkeypatch, Recorder(respond))
    out = survey.survey_search(["notes"], "q")
    assert "summaries missing for 1 of 3 documents in notes; run artmind ingest summarise --domain notes" in out["warnings"]


def test_legs_can_be_switched_off_for_the_eval(monkeypatch):
    s = Recorder(respond)
    _patch(monkeypatch, s)
    out = survey.survey_search(["notes"], "q", legs=("P",))
    assert [d["doc_id"] for d in out["documents"]] == ["B", "A"]
    assert not s.queries_containing("summary_embedding")


def test_top_docs_cuts_and_topics_are_asked_for_the_returned_ids(monkeypatch):
    _patch(monkeypatch, Recorder(respond))
    seen = {}

    def topics(doc_ids, domains):
        seen["args"] = (doc_ids, domains)
        return [{"slug": "beta", "overlap": 2}]

    out = survey.survey_search(["notes"], "q", top_docs=2, topics=topics)
    assert [d["doc_id"] for d in out["documents"]] == ["B", "C"]
    assert seen["args"] == (["B", "C"], ["notes"]) and out["topics"] == [{"slug": "beta", "overlap": 2}]


def test_embedding_down_keeps_the_keyword_legs(monkeypatch):
    def down(q):
        raise RuntimeError("ollama unreachable")

    s = Recorder(respond)
    _patch(monkeypatch, s, embed=down)
    out = survey.survey_search(["notes"], "q")
    assert out["warnings"][0].startswith("vector leg unavailable")
    assert not s.queries_containing("VECTOR INDEX")
    assert [d["doc_id"] for d in out["documents"]] == ["B"]


def test_a_missing_summary_index_names_setup(monkeypatch):
    def missing(cypher, params):
        if "summary_embedding" in cypher:
            raise ClientError("There is no such vector schema index: summary_embedding")
        return respond(cypher, params)

    _patch(monkeypatch, Recorder(missing))
    with pytest.raises(ValueError, match="artmind setup"):
        survey.survey_search(["notes"], "q")


@pytest.mark.parametrize("kwargs", [{"top_docs": 0}, {"top_docs": 51}, {"passages": 0}, {"passages": 9}, {"legs": ()}])
def test_bad_arguments_raise(monkeypatch, kwargs):
    _patch(monkeypatch, Recorder(respond))
    with pytest.raises(ValueError):
        survey.survey_search(["notes"], "q", **kwargs)


def test_survey_keyword_query_drops_stopwords(monkeypatch):
    """Survey strips stopwords from its fulltext query so common function words don't
    dilute the BM25 score (change 2 in 2026-11-01 spec)."""
    s = Recorder(respond)
    _patch(monkeypatch, s)
    survey.survey_search(["notes"], "Which amendments applied an annual widget fee or increase")
    ft_calls = s.queries_containing("summary_text_ft")
    assert ft_calls, "at least one fulltext summary query must be issued"
    _, params = ft_calls[0]
    ft_query = params["ft_query"]
    # Stopwords "which", "or", "an" must be absent; content words must remain
    for stopword in ("which", "or", "an"):
        assert stopword not in ft_query.split(), (
            f"stopword {stopword!r} found in survey fulltext query: {ft_query!r}"
        )
    for content in ("amendments", "applied", "annual", "widget", "fee", "increase"):
        assert content in ft_query, f"content word {content!r} missing from survey query: {ft_query!r}"


def test_survey_keyword_all_stopwords_keeps_original(monkeypatch):
    """When all terms are stopwords the query is kept unchanged (never empty)."""
    s = Recorder(respond)
    _patch(monkeypatch, s)
    # "the" and "a" are both stopwords; with only stopwords we should keep them
    survey.survey_search(["notes"], "the a")
    ft_calls = s.queries_containing("summary_text_ft")
    _, params = ft_calls[0]
    # Original kept intact (not stripped to empty)
    assert params["ft_query"] == graph_query.sanitize_lucene_query("the a")
