"""Bundle adapters: one command's payload -> evidence / documents / facts / guides rows (P3 spec §6.2)."""

from artmind.query_run import adapters

DOC = {"id": "d1", "title": "Fees", "domain": "bank", "date": "2026-01-02", "status": "current"}


def test_vector_text_brief_rows_are_passages():
    rows = adapters.adapt({"command": "query vector-text", "rows": [
        {"rank": 1, "passage_id": "p1", "chunk_id": "c1", "text": "fee text", "relevance": 0.7, "doc": DOC}]})
    (ev,) = rows.evidence
    assert ev == {"kind": "passage", "text": "fee text", "passage_id": "p1", "chunk_id": "c1", "relevance": 0.7, "doc": DOC}


def test_vector_text_full_rows_read_chunk_and_document():
    rows = adapters.adapt({"command": "query vector-text", "rows": [
        {"chunk": {"id": "c1", "text": "full", "doc_id": "d1"}, "passage": {"id": "p1"}, "relevance": 0.5,
         "document": {"id": "d1", "name": "fees.md", "_domain": "bank", "valid_from": "2026-01-02", "status": "current"}}]})
    (ev,) = rows.evidence
    assert ev["passage_id"] == "p1" and ev["doc"]["title"] == "fees" and ev["doc"]["date"] == "2026-01-02"


def test_text_is_capped():
    rows = adapters.adapt({"command": "query vector-text", "rows": [{"chunk_id": "c1", "text": "x" * 5000, "doc": DOC}]})
    assert len(rows.evidence[0]["text"]) <= adapters.PASSAGE_CHARS


def test_survey_rows_are_documents_and_their_passage_is_evidence():
    rows = adapters.adapt({"command": "query survey", "topics": [{"slug": "fees", "title": "Fees", "stale": False}],
                           "documents": [
        {"rank": 1, "doc_id": "d1", "title": "Fees", "domain": "bank", "date": None, "status": "current",
         "gist": "g", "gist_source": "passage",
         "passage": {"passage_id": "p1", "chunk_id": "c1", "text": "t", "relevance": 0.6}},
        {"rank": 2, "doc_id": "d2", "title": "Other", "date": None, "status": "current"}]})
    assert [d["doc_id"] for d in rows.documents] == ["d1", "d2"]
    assert rows.evidence[0]["passage_id"] == "p1" and rows.evidence[0]["doc"]["id"] == "d1"
    assert rows.guides == [{"kind": "topic_page", "slug": "fees", "title": "Fees", "stale": False}]


def test_entity_context_brief_gives_a_fact_and_its_chunks():
    rows = adapters.adapt({"command": "query entity-context", "rows": [
        {"entity": {"id": "e1", "name": "Acme", "class": "ORG", "description": "a bank"},
         "connections": [{"id": "e2", "name": "Bob", "class": "PERSON"}] * 30, "connections_total": 30,
         "chunks": [{"rank": 1, "passage_id": None, "chunk_id": "c9", "text": "acme text", "relevance": None, "doc": DOC}],
         "source_documents": [{"id": "d1", "title": "Fees", "date": None}]}]})
    (fact,) = rows.facts
    assert fact["kind"] == "entity" and fact["row"]["entity"]["name"] == "Acme"
    assert len(fact["row"]["connections"]) == adapters.CONNECTIONS and fact["chunk_ids"] == ["c9"]
    assert rows.evidence[0]["chunk_id"] == "c9" and rows.documents[0]["doc_id"] == "d1"


def test_pattern10_chunks_carry_document_status():
    rows = adapters.adapt({"command": "query graph pattern10", "rows": [
        {"document": {"id": "d1", "name": "Policy v1.md", "superseded_by": "d2"},
         "chunk": {"id": "c1", "text": "old", "doc_id": "d1"}}]})
    assert rows.evidence[0]["doc"]["status"] == "superseded" and rows.evidence[0]["kind"] == "chunk"


def test_sql_rows_are_facts():
    rows = adapters.adapt({"command": "query text2sql", "rows": [{"n": 3}],
                           "source_tables": [{"table": "fees", "source_file": "tables/fees.csv"}]})
    assert rows.facts == [{"kind": "sql_row", "row": {"n": 3}, "chunk_ids": []}]


def test_insights_findings_are_guides():
    rows = adapters.adapt({"command": "query insights list", "findings": [{"key": "k1", "title": "T", "score": 0.4,
                                                                           "subjects": [{"_id": "e1"}]}]})
    assert rows.guides[0]["kind"] == "insight" and rows.guides[0]["key"] == "k1"


def test_an_unknown_command_goes_to_facts_with_scalars_only():
    rows = adapters.adapt({"command": "query graph hierarchy", "rows": [{"name": "A", "level": 2, "kids": [1, 2, 3],
                                                                         "blob": {"x": 1}}]})
    assert rows.facts == [{"kind": "row", "row": {"name": "A", "level": 2, "kids_count": 3}, "chunk_ids": []}]


def test_every_routes_yaml_command_has_an_adapter():
    import shlex

    from artmind.query_plan import program as prog, routes
    from artmind.query_run import capture

    for route in routes.load_routes():
        templates = [n.run for c in route.programs for n in prog.run_nodes(c.program)]
        templates += [t for v in route.variants.values() for t in v] + list(route.optional_steps)
        for template in templates:
            argv = shlex.split(routes.render(template, {k: "x" for k in (*routes.ALL_PLACEHOLDERS, "phrase", "twin", "doc")}))[1:]
            key = capture.command_key(argv)
            assert key in adapters.ADAPTERS, (route.id, key)
