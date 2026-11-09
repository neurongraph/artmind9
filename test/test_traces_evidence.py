"""artmind.traces.evidence.summarize over real query payload shapes."""

from artmind.traces.evidence import summarize

VECTOR_TEXT = {
    "query_type": "vector_text", "domains": ["banking"], "question": "q", "parameters": {},
    "rows": [
        {"score": 0.8, "source_type": "document",
         "chunk": {"id": "c1", "name": "c1", "doc_id": "d1", "text": "…"},
         "document": {"id": "d1", "name": "Policy", "path": "banking/Policy.md", "source_path": "banking/Policy.md", "_domain": "banking"}},
        {"score": 0.5, "source_type": "user_chat", "chat": {"id": "u1", "raw_text": "…"}},
    ],
}

PATTERN2 = {
    "query_type": "graph", "command": "pattern", "pattern": "pattern2", "rows": [
        {"entityData": {"_id": "e1", "name": "Alice", "label": "Person"},
         "doc_sources": [{"id": "c9", "name": "c9", "doc_id": "d9", "_domain": "b", "source_type": "document"}],
         "source_documents": [{"id": "d9", "name": "Org", "path": "b/Org.md", "source_path": "b/Org.md"}]},
    ],
}

TEXT2CYPHER = {"command": "text2cypher", "generated_cypher": "MATCH (n) RETURN n", "rows": [{"n": 1}],
               "rows_total": 7, "truncated": True}

VIEWS_RUN_OK = {"view": "org_tree", "version": 2, "domains": ["b"], "status": "ok",
                "params": {"root": {"input": "Alice", "_id": "e1"}}, "rows": [{"x": 1}, {"x": 2}],
                "rows_total": 2, "truncated": False}

VIEWS_RUN_AMBIGUOUS = {"view": "org_tree", "version": 2, "status": "needs_disambiguation", "param": "root",
                       "candidates": [{"_id": "e1", "name": "Alice"}, {"_id": "e2", "name": "Alicia"}]}

ENTITY_RESOLVE = {"query_type": "entity_resolve", "rows": [
    {"entity": {"_id": "e1", "name": "Alice", "entity_class": "Person"}, "score": 0.033, "matched_by": ["vector"]},
    {"entity": {"_id": "e2", "name": "Alicia", "entity_class": "Person"}, "score": 0.031, "matched_by": ["fulltext"]},
]}

HIERARCHY = {"command": "hierarchy", "root": {"_id": "e1"}, "rows": [{"_id": "e2", "depth": 1}],
             "summary": {"total": 40, "truncated": True}}

TEXT2SQL = {"command": "text2sql", "generated_sql": "SELECT 1", "rows": [{"a": 1}],
            "source_tables": [{"table": "loans", "source_file": "tables/loans.csv"}]}

CONFLICTS = {"command": "conflicts", "rows": [{
    "conflict": {"id": "k1"}, "entities": [{"_id": "e5", "name": "X"}],
    "evidence": [{"side": "a", "chunk_id": "c5", "doc_id": "d5", "text": "…",
                  "document": {"name": "N", "path": "b/N.md", "source_path": "b/N.md"}}]}]}


def test_vector_text_rows_chunks_and_documents():
    s = summarize(VECTOR_TEXT)
    assert s["rows"] == 2
    assert s["truncated"] is False
    assert s["evidence"]["chunks"] == [{"chunk_id": "c1", "doc_id": "d1"}]
    assert s["evidence"]["documents"] == [{"doc_id": "d1", "path": "banking/Policy.md"}]
    provenance = s["evidence"]["provenance"]
    assert provenance["documents"] == [{
        "doc_id": "d1", "path": "banking/Policy.md", "source_path": "banking/Policy.md", "domain": "banking",
        "chunks": [{"chunk_id": "c1", "sequence": None, "name": "c1", "header_path": None, "passages": []}],
    }]


def test_pattern2_entities_chunks_documents():
    ev = summarize(PATTERN2)["evidence"]
    assert ev["entities"] == ["e1"]
    assert ev["chunks"] == [{"chunk_id": "c9", "doc_id": "d9"}]
    assert ev["documents"] == [{"doc_id": "d9", "path": "b/Org.md"}]
    assert ev["provenance"]["documents"][0]["chunks"][0]["chunk_id"] == "c9"


def test_text2cypher_cypher_and_truncation():
    s = summarize(TEXT2CYPHER)
    assert s["evidence"]["cypher"] == "MATCH (n) RETURN n"
    assert s["truncated"] is True
    assert s["rows_total"] == 7


def test_views_run_ok_and_disambiguation():
    ok = summarize(VIEWS_RUN_OK)
    assert ok["rows"] == 2
    assert ok["evidence"]["view"] == {"name": "org_tree", "version": 2, "status": "ok",
                                      "params": {"root": {"input": "Alice", "_id": "e1"}}}
    amb = summarize(VIEWS_RUN_AMBIGUOUS)
    assert amb["rows"] == 0  # a disambiguation prompt is not an answer
    assert amb["evidence"]["resolved"] == [{"id": "e1", "name": "Alice", "entity_class": None, "score": None},
                                           {"id": "e2", "name": "Alicia", "entity_class": None, "score": None}]


def test_entity_resolve_candidates_in_order_with_scores():
    ev = summarize(ENTITY_RESOLVE)["evidence"]
    assert ev["resolved"] == [{"id": "e1", "name": "Alice", "entity_class": "Person", "score": 0.033},
                              {"id": "e2", "name": "Alicia", "entity_class": "Person", "score": 0.031}]


def test_hierarchy_truncation_lives_in_summary():
    s = summarize(HIERARCHY)
    assert s["truncated"] is True
    assert s["rows_total"] == 40


def test_text2sql_sql_and_source_files():
    ev = summarize(TEXT2SQL)["evidence"]
    assert ev["sql"] == "SELECT 1"
    assert ev["documents"] == [{"doc_id": None, "path": "tables/loans.csv"}]


def test_conflict_evidence_chunk_ids():
    ev = summarize(CONFLICTS)["evidence"]
    assert ev["chunks"] == [{"chunk_id": "c5", "doc_id": "d5"}]
    assert ev["entities"] == ["e5"]


def test_lists_are_deduplicated_and_capped():
    payload = {"rows": [{"entityData": {"_id": f"e{i % 300}"}} for i in range(600)]}
    ev = summarize(payload)["evidence"]
    assert len(ev["entities"]) == 200
    assert len(set(ev["entities"])) == 200


def test_non_dict_payloads_do_not_raise():
    assert summarize([1, 2])["rows"] == 2
    assert summarize("text")["rows"] == 0


ENTITY_HISTORY = {
    "query_type": "graph", "command": "entity_history", "entity_id": "e1",
    "rows": [{"observation": {"id": "obs1", "doc_id": "d1", "key": "person_alice", "name": "Alice",
                              "_valid_from": "2024-01-01", "role": "Director"}}],
}


def test_entity_history_observations_are_not_chunks():
    s = summarize(ENTITY_HISTORY)
    assert s["rows"] == 1
    assert "chunks" not in s["evidence"]


ENTITY_CONTEXT = {"command": "entity_context", "rows": [{
    "entityData": {"_id": "e1", "name": "Alice"},
    "chunks": [{"id": "c1", "name": "c1", "doc_id": "d1", "_domain": "b", "text": "t",
                "document": {"id": "d1", "name": "P", "path": "b/P.md", "source_path": "b/P.md"}}],
    "more_chunks": [{"id": "c2", "name": "c2", "doc_id": "d1"}],
    "source_documents": [{"id": "d1", "name": "P", "path": "b/P.md", "source_path": "b/P.md"}],
}]}


def test_entity_context_chunks_documents_entity():
    ev = summarize(ENTITY_CONTEXT)["evidence"]
    assert ev["chunks"] == [{"chunk_id": "c1", "doc_id": "d1"}, {"chunk_id": "c2", "doc_id": "d1"}]
    assert ev["documents"] == [{"doc_id": "d1", "path": "b/P.md"}]
    assert ev["entities"] == ["e1"]


def test_insights_payloads_count_as_answers():
    from artmind.traces.evidence import summarize

    listing = {"domain": "d", "as_of": "2026-10-01", "findings": [{"key": "a", "subjects": [{"_id": "p1"}]},
                                                                  {"key": "b", "subjects": []}]}
    assert summarize(listing)["rows"] == 2
    assert summarize({"domain": "d", "finding": {"key": "a"}})["rows"] == 1
    assert summarize({"domain": "d", "findings": []})["rows"] == 0


def _plan_payload(**over):
    base = {"plan_version": 1, "question": "q",
            "domains": {"selected": ["b"], "candidates": [{"name": "b", "_id": "dom1"}]},
            "view_match": {"candidates": [{"name": "org_tree", "_id": "v1"}]},
            "entities": [{"mention": "Alice", "candidates": [{"_id": "e1", "name": "Alice"}]}],
            "store": {"tables": [{"source_file": "t.csv", "_id": "s1"}]},
            "executed": None, "rows": [], "rows_total": 0, "truncated": False}
    return {**base, **over}


def test_a_plan_that_ran_nothing_has_no_evidence_from_its_candidates():
    out = summarize(_plan_payload())
    assert out["rows"] == 0
    assert out["evidence"] == {}


def test_an_executed_plan_records_the_view_and_the_row_ids():
    result = {"view": "org_tree", "version": 2, "status": "ok", "domains": ["b"],
              "params": {"root": {"input": "Alice", "_id": "e1"}}}
    out = summarize(_plan_payload(executed={"cmd": "x", "status": "ok", "error": None, "result": result},
                                  rows=[{"person": {"_id": "e7", "name": "Bo"}}], rows_total=1))
    assert out["rows"] == 1
    assert out["evidence"]["view"] == {"name": "org_tree", "version": 2, "status": "ok",
                                       "params": {"root": {"input": "Alice", "_id": "e1"}}}
    assert set(out["evidence"]["entities"]) == {"e1", "e7"}  # the resolved param, as in a plain views run
    assert "dom1" not in out["evidence"]["entities"] and "v1" not in out["evidence"]["entities"]


def test_survey_and_topics_payloads_have_evidence():
    from artmind.traces import evidence

    survey = {"query_type": "survey", "documents": [
        {"doc_id": "d1", "passages": [{"id": "p1", "chunk_id": "d1_0"}]}], "topics": [{"slug": "alpha"}]}
    ev = evidence.summarize(survey)["evidence"]
    assert ev["documents"] == [{"doc_id": "d1", "path": None}]
    assert ev["passages"] == ["p1"]
    assert {"chunk_id": "d1_0", "doc_id": "d1"} in ev["chunks"]
    assert ev["topics"] == ["alpha"]
    assert evidence.summarize({"pages": [{"slug": "a"}, {"slug": "b"}]})["evidence"]["topics"] == ["a", "b"]
    assert evidence.summarize({"page": {"slug": "a"}})["evidence"]["topics"] == ["a"]


BRIEF_VECTOR_TEXT = {
    "query_type": "vector_text",
    "rows": [{
        "rank": 1, "passage_id": "d1_027_p03", "chunk_id": "d1_027", "text": "t",
        "doc": {"id": "d1", "title": "Contract One", "domain": "banking", "date": None, "status": "current"},
    }],
}

BRIEF_SURVEY = {
    "query_type": "survey",
    "documents": [
        {"rank": 1, "doc_id": "d1", "title": "Contract One", "domain": "banking",
         "best": {"passage_id": "d1_002_p02", "chunk_id": "d1_002"}},
        {"rank": 2, "doc_id": "d2", "title": "Line Only", "status": "current"},
    ],
}


def test_brief_vector_text_rows_give_provenance_by_doc_id():
    provenance = summarize(BRIEF_VECTOR_TEXT)["evidence"]["provenance"]
    (doc,) = provenance["documents"]
    assert (doc["doc_id"], doc["domain"], doc["title"], doc["path"], doc["source_path"]) == (
        "d1", "banking", "Contract One", None, None)
    assert [c["chunk_id"] for c in doc["chunks"]] == ["d1_027"]


def test_brief_survey_cites_documents_with_a_best_chunk_not_line_rows():
    provenance = summarize(BRIEF_SURVEY)["evidence"]["provenance"]
    assert [d["doc_id"] for d in provenance["documents"]] == ["d1"]
    assert [c["chunk_id"] for c in provenance["documents"][0]["chunks"]] == ["d1_002"]


def test_brief_chunks_rows_give_provenance():
    payload = {"query_type": "chunks", "rows": [{
        "chunk": {"id": "d1_003", "name": "Chunk 3/9", "header_path": "H"},
        "doc": {"id": "d1", "title": "Contract One", "domain": "banking"}}]}
    (doc,) = summarize(payload)["evidence"]["provenance"]["documents"]
    assert doc["doc_id"] == "d1" and doc["chunks"][0]["chunk_id"] == "d1_003"


def test_a_no_match_status_is_kept_in_evidence():
    from artmind.traces.evidence import summarize

    out = summarize({"query_type": "entity_resolve", "status": "no_match", "rows": []})
    assert out["rows"] == 0
    assert out["evidence"]["status"] == "no_match"


def test_needs_disambiguation_status_is_kept():
    from artmind.traces.evidence import summarize

    out = summarize({"query_type": "entity_resolve", "status": "needs_disambiguation", "rows": [{"x": 1}]})
    assert out["evidence"]["status"] == "needs_disambiguation"


def test_an_ordinary_status_is_not_recorded():
    from artmind.traces.evidence import summarize

    assert "status" not in summarize({"status": "ok", "rows": [{"x": 1}]})["evidence"]


def test_warnings_are_kept_capped_and_trimmed():
    from artmind.traces.evidence import summarize

    long = "unknown domain 'zz': " + "x" * 600
    out = summarize({"rows": [], "warnings": [long, 7, "second"] + ["w"] * 30})
    ws = out["evidence"]["warnings"]
    assert ws[0].startswith("unknown domain 'zz'") and len(ws[0]) <= 300
    assert "second" in ws and 7 not in ws
    assert len(ws) <= 10


def test_no_warnings_key_when_the_list_is_empty():
    from artmind.traces.evidence import summarize

    assert "warnings" not in summarize({"rows": [], "warnings": []})["evidence"]
