"""Step facts: every step predicate from a payload (P3 spec §3.4). Invented payloads."""

import math

from artmind.query_plan import program as program_mod
from artmind.query_run import facts


def _passage(doc, rel, text="t"):
    return {"rank": 1, "passage_id": f"p-{doc}-{rel}", "chunk_id": f"c-{doc}", "text": text, "relevance": rel,
            "doc": {"id": doc, "title": doc.upper(), "domain": "bank", "date": None, "status": "current"}}


VECTOR = {"command": "query vector-text", "rows": [_passage("d1", 0.9), _passage("d1", 0.5), _passage("d2", 0.4)]}
VECTOR_ARGV = ["query", "vector-text", "--shape", "brief", "--domain", "bank", "--topK", "10", "fees", "--compact"]
SURVEY = {"command": "query survey", "documents": [
    {"rank": 1, "doc_id": "s1", "title": "Rates", "gist": "g"}, {"rank": 2, "doc_id": "s2", "title": "Other"}]}
PATTERN7 = {"command": "query graph pattern7", "rows": [{"id": "e1", "documents": [{"id": "d9", "title": "X"}]}],
            "rows_total": 80, "truncated": True}


def test_every_predicate_name_is_present():
    got = facts.step_facts(VECTOR, VECTOR_ARGV)
    assert set(program_mod.PREDICATE_NAMES) <= set(got)


def test_vector_text_facts_follow_the_needle_rule():
    got = facts.step_facts(VECTOR, VECTOR_ARGV)
    assert got["rows"] == 3 and got["rows_total"] == 3 and got["truncated"] is False
    assert got["distinct_documents"] == 2 and got["docs_cited"] == 2
    assert got["score_drop"] == 0.4 and got["max_relevance"] == 0.9
    assert got["rows_below_k"] == 1  # 3 rows < topK 10


def test_survey_rows_are_documents_not_citations():
    got = facts.step_facts(SURVEY, ["query", "survey", "q"])
    assert got["rows"] == 2 and got["distinct_documents"] == 2 and got["docs_cited"] == 0
    assert got["score_drop"] is None and got["max_relevance"] is None


def test_envelope_totals_win():
    got = facts.step_facts(PATTERN7, ["query", "graph", "pattern7", "--limit", "50"])
    assert (got["rows"], got["rows_total"], got["truncated"]) == (1, 80, True)


def test_entity_docs_from_entity_context():
    payload = {"command": "query entity-context", "rows": [{"entity": {"id": "e1"}, "source_documents_total": 4,
                                                             "chunks": [_passage("d1", None)]}]}
    got = facts.step_facts(payload, ["query", "entity-context"])
    assert got["entity_docs"] == 4 and got["docs_cited"] == 1


def test_an_empty_or_missing_payload_reads_as_nothing():
    got = facts.step_facts(None, [])
    assert got["rows"] == 0 and got["truncated"] is False and got["distinct_documents"] == 0


def test_merge_unions_documents():
    a = facts.step_facts(VECTOR, VECTOR_ARGV)
    b = facts.step_facts(SURVEY, ["query", "survey", "q"])
    m = facts.merge([a, b])
    assert m["rows"] == 5 and m["distinct_documents"] == 4 and m["docs_cited"] == 2
    assert facts.merge([])["rows"] == 0


def test_public_drops_the_private_sets():
    assert not any(k.startswith("_") for k in facts.public(facts.step_facts(VECTOR, VECTOR_ARGV)))


def test_evaluate_qualified_and_default_nodes():
    by_node = {"needle": facts.step_facts(VECTOR, VECTOR_ARGV), "s1": facts.step_facts(PATTERN7, ["query"])}
    pred = program_mod.parse_predicate("needle.score_drop < NEEDLE_MARGIN or needle.distinct_documents >= NEEDLE_SPREAD", "t")
    assert facts.evaluate(pred, by_node, "u") is False  # clear winner (0.4 >= 0.03) in 2 < 7 documents
    assert facts.evaluate(program_mod.parse_predicate("truncated", "t"), by_node, "s1") is True
    assert facts.evaluate(program_mod.parse_predicate("rows == 0", "t"), by_node, "missing") is True


def test_a_missing_score_drop_reads_as_minus_infinity():
    one = {"command": "query vector-text", "rows": [_passage("d1", 0.9)]}
    by_node = {"needle": facts.step_facts(one, VECTOR_ARGV)}
    assert by_node["needle"]["score_drop"] is None
    pred = program_mod.parse_predicate("needle.score_drop < NEEDLE_MARGIN", "t")
    assert facts.evaluate(pred, by_node, "u") is True
    assert facts.number(None, "score_drop") == -math.inf


def test_doc_key_prefers_ids():
    assert facts.doc_key({"id": "d1", "path": "a.md"}) == "d1"
    assert facts.doc_key({"path": "a.md"}) == "a.md"
    assert facts.doc_key({}) is None
