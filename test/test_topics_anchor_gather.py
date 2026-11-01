# test/test_topics_anchor_gather.py
from _neo4j_recorder import Recorder

from artmind.topics import anchor, gather


def _resolve(rows):
    return lambda domains, ref, top_k: {"rows": rows}


ENTITY_ROW = {"key": "PERSON|acme|journal", "name": "Acme", "entity_class": "ORG", "description": "A supplier.",
              "observation_set_hash": "h1"}


def _session():
    return Recorder(lambda c, p: [ENTITY_ROW] if "MATCH (e:Entity {_id: $id})" in c else [])


def _context(domains, entity_id, include_chunks, query=None):
    return {"rows": [{"chunks": [{"id": "c9", "doc_id": "D9", "text": "Acme signed."}]}]}


def test_chunks_only_scopes_are_topic_anchored():
    assert anchor.choose_anchor("Acme", graph_domains=[], resolve=_resolve([])) == {"kind": "topic"}


def test_a_single_exact_match_anchors_on_the_entity():
    rows = [{"entity": {"_id": "e1"}, "matched_by": ["exact", "fulltext"], "score": 0.016}]
    out = anchor.choose_anchor("Acme", graph_domains=["journal"], resolve=_resolve(rows),
                               context=_context, session_factory=_session)
    assert out["kind"] == "entity" and out["id"] == "e1" and out["key"] == "PERSON|acme|journal"
    assert out["observation_set_hash"] == "h1" and out["chunks"] == [{"id": "c9", "doc_id": "D9", "text": "Acme signed."}]


def test_a_clear_margin_anchors_and_a_close_race_does_not():
    clear = [{"entity": {"_id": "e1"}, "matched_by": ["vector", "fulltext"], "score": 0.0328},
             {"entity": {"_id": "e2"}, "matched_by": ["vector"], "score": 0.016}]
    close = [{"entity": {"_id": "e1"}, "matched_by": ["vector", "fulltext"], "score": 0.0328},
             {"entity": {"_id": "e2"}, "matched_by": ["vector", "fulltext"], "score": 0.0320}]
    assert anchor.choose_anchor("x", graph_domains=["j"], resolve=_resolve(clear), context=_context,
                                session_factory=_session)["kind"] == "entity"
    assert anchor.choose_anchor("x", graph_domains=["j"], resolve=_resolve(close))["kind"] == "topic"


def test_two_exact_matches_do_not_anchor():
    rows = [{"entity": {"_id": "e1"}, "matched_by": ["exact"]}, {"entity": {"_id": "e2"}, "matched_by": ["exact"]}]
    assert anchor.choose_anchor("Acme", graph_domains=["j"], resolve=_resolve(rows))["kind"] == "topic"


SURVEY = {"documents": [
    {"doc_id": "D1", "title": "One", "name": "one.md", "domain": "j", "summary": "S1",
     "passages": [{"id": "c1_p01", "chunk_id": "c1", "text": "a" * 6000}, {"id": "c1_p02", "chunk_id": "c1", "text": "b" * 6000}]},
    {"doc_id": "D2", "title": "Two", "name": "two.md", "domain": "j", "summary": None,
     "passages": [{"id": "c2_p01", "chunk_id": "c2", "text": "two"}]},
]}


def _links_session():
    def respond(cypher, params):
        if "UNWIND $chunkIds" in cypher:
            return [{"chunk_id": c, "header_path": "H", "doc_id": f"D{c[1:]}", "name": f"n{c}.md",
                     "title": f"T{c}", "path": f"p/{c}.md", "source_path": None, "version": f"v{c}"}
                    for c in params["chunkIds"]]
        if "UNWIND $docIds" in cypher:
            return [{"doc_id": d, "title": "Nine", "name": "nine.md", "domain": "j", "summary": "S9"} for d in params["docIds"]]
        return []
    return Recorder(respond)


def test_gather_takes_survey_documents_and_caps_each_documents_text():
    seen = {}

    def search(domains, topic, top_docs, passages):
        seen.update(top_docs=top_docs, passages=passages)
        return SURVEY

    materials, links = gather.gather("t", ["j"], {"kind": "topic"}, 12, search=search, session_factory=_links_session)
    assert seen == {"top_docs": 12, "passages": gather.MAX_PASSAGES_PER_DOC}
    assert [m.doc_id for m in materials] == ["D1", "D2"]
    assert [s.id for s in materials[0].sources] == ["c1_p01"]          # the second passage would pass 10000 chars
    assert links["c1_p01"]["path"] == "p/c1.md" and links["c2_p01"]["version"] == "vc2"


def test_an_entity_anchor_adds_its_evidence_documents():
    entity = {"kind": "entity", "chunks": [{"id": "c9", "doc_id": "D9", "text": "Acme signed."},
                                           {"id": "c2", "doc_id": "D2", "text": "two again"}]}
    materials, links = gather.gather("t", ["j"], entity, 12, search=lambda *a, **k: SURVEY,
                                     session_factory=_links_session)
    assert [m.doc_id for m in materials] == ["D1", "D2", "D9"]
    assert [s.id for s in materials[1].sources] == ["c2_p01", "c2"]
    assert materials[2].title == "Nine" and [s.id for s in materials[2].sources] == ["c9"]
    assert "c9" in links


def test_the_document_cap_holds_for_entity_evidence():
    entity = {"kind": "entity", "chunks": [{"id": "c9", "doc_id": "D9", "text": "x"}]}
    materials, _ = gather.gather("t", ["j"], entity, 2, search=lambda *a, **k: SURVEY, session_factory=_links_session)
    assert [m.doc_id for m in materials] == ["D1", "D2"]
