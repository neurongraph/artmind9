"""Out-of-scope check (artmind/scope_check.py): vector-text and survey report documents
outside the searched domains that match the question more closely (banking Q33, 2026-11-10)."""
from _neo4j_recorder import Recorder

from artmind import graph_query, hints, scope_check, survey, vector_query

FAMILY = {
    "bank": ["bank", "bank.policy", "bank.sop", "bank.governance"],
    "bank.sop": ["bank.sop"],
    "bank.policy": ["bank.policy"],
    "notes": ["notes"],
}

INSIDE = {"kind": "passage", "id": "s1_p01", "chunk_id": "s1", "doc_id": "SOP", "text": "sop matrix", "cosine": 0.875}
OUTSIDE = [
    {"kind": "passage", "id": "p1_p01", "chunk_id": "p1", "doc_id": "POL", "text": "policy matrix", "cosine": 0.896},
    {"kind": "passage", "id": "p2_p01", "chunk_id": "p2", "doc_id": "POL", "text": "policy again", "cosine": 0.86},
    {"kind": "passage", "id": "g1_p01", "chunk_id": "g1", "doc_id": "GOV", "text": "weaker", "cosine": 0.70},
]


def respond(cypher, params):
    if "passage_embedding" in cypher:
        rows = [INSIDE] if params["domains"] == ["bank.sop"] else OUTSIDE
        return [{"row": r} for r in rows]
    if "MATCH (d:Document {id: id})" in cypher:
        return [{"doc_id": d, "title": {"POL": "policy_complaints_v3", "GOV": "charter"}[d], "domain":
                 {"POL": "bank.policy", "GOV": "bank.governance"}[d]} for d in params["docIds"]]
    if "MATCH (c:DocChunk {id: cid})" in cypher:
        return [{"chunk": {"id": "s1", "doc_id": "SOP", "text": "sop matrix"},
                 "document": {"id": "SOP", "title": "escalation_matrix", "_domain": "bank.sop", "superseded": False}}]
    return []


def _family(monkeypatch):
    monkeypatch.setattr(graph_query, "expand_domain_family", lambda d: FAMILY.get(d, [d]))


def test_outside_domains_are_the_rest_of_each_root(monkeypatch):
    _family(monkeypatch)
    assert scope_check.roots(["bank.sop", "bank.policy", "notes"]) == ["bank", "notes"]
    assert scope_check.outside_domains(["bank.sop"]) == ["bank", "bank.policy", "bank.governance"]
    assert scope_check.outside_domains(["bank"]) == []


def test_only_documents_beating_the_best_returned_row_are_reported(monkeypatch):
    _family(monkeypatch)
    s = Recorder(respond)
    found = scope_check.outside_scope(s, ["bank.sop"], [1.0, 0.0], best_relevance=0.875)
    assert found == [{"doc_id": "POL", "title": "policy_complaints_v3", "domain": "bank.policy", "relevance": 0.896,
                      "passage_id": "p1_p01", "chunk_id": "p1", "passage": "policy matrix"}]
    (_, params), = s.queries_containing("passage_embedding")
    assert params["domains"] == ["bank", "bank.policy", "bank.governance"]
    assert params["pool"] == scope_check.OUTSIDE_POOL and params["embedding"] == [1.0, 0.0]
    (_, fetch), = s.queries_containing("MATCH (d:Document {id: id})")
    assert fetch["docIds"] == ["POL"]


def test_no_query_when_the_search_covers_its_root_or_has_no_embedding(monkeypatch):
    _family(monkeypatch)
    s = Recorder(respond)
    assert scope_check.outside_scope(s, ["bank"], [1.0], best_relevance=0.5) == []
    assert scope_check.outside_scope(s, ["bank.sop"], None, best_relevance=0.5) == []
    assert s.calls == []


def test_an_empty_vector_result_counts_any_outside_match(monkeypatch):
    _family(monkeypatch)
    found = scope_check.outside_scope(Recorder(respond), ["bank.sop"], [1.0], best_relevance=None)
    assert [f["doc_id"] for f in found] == ["POL", "GOV"]


def test_vector_text_reports_outside_scope_with_a_warning(monkeypatch):
    _family(monkeypatch)
    monkeypatch.setattr(vector_query, "embed_question", lambda q: [1.0, 0.0])
    monkeypatch.setattr(vector_query, "read_session", lambda: Recorder(respond))
    out = vector_query.vector_text_search(["bank.sop"], "complaint escalation matrix", 5, shape="brief")
    assert out["outside_scope"][0]["title"] == "policy_complaints_v3"
    assert any("outside the searched domains" in w and "policy_complaints_v3" in w for w in out["warnings"])
    nxt = hints.outside_scope_next(out, "vector-text", "complaint escalation matrix", shape="brief",
                                   options="--topK 5")
    assert nxt == ("artmind query vector-text --shape brief --domain bank --topK 5 --compact "
                   "'complaint escalation matrix'")


def test_vector_text_on_the_root_has_no_outside_scope_key(monkeypatch):
    _family(monkeypatch)
    monkeypatch.setattr(vector_query, "embed_question", lambda q: [1.0, 0.0])
    monkeypatch.setattr(vector_query, "read_session", lambda: Recorder(respond))
    out = vector_query.vector_text_search(["bank"], "complaint escalation matrix", 5)
    assert "outside_scope" not in out
    assert hints.outside_scope_next(out, "vector-text", "q") is None


def _survey_respond(cypher, params):
    if "summary_embedding" in cypher or "summary_text_ft" in cypher or "count(d) AS total" in cypher:
        return []
    if "UNWIND $docIds" in cypher and "summary" in cypher:
        return [{"doc_id": d, "title": d, "name": d, "domain": "bank.sop", "summary": None} for d in params["docIds"]]
    return respond(cypher, params)


def test_survey_reports_outside_scope_and_skips_it_for_a_date_window(monkeypatch):
    _family(monkeypatch)
    monkeypatch.setattr(vector_query, "embed_question", lambda q: [1.0, 0.0])
    s = Recorder(_survey_respond)
    monkeypatch.setattr(survey, "read_session", lambda: s)
    out = survey.survey_search(["bank.sop"], "complaint escalation matrix", top_docs=10)
    assert out["outside_scope"][0]["doc_id"] == "POL"
    assert any("outside the searched domains" in w for w in out["warnings"])
    assert hints.outside_scope_next(out, "survey", "complaint escalation matrix", shape="brief") == (
        "artmind query survey --shape brief --domain bank --compact 'complaint escalation matrix'")

    s2 = Recorder(_survey_respond)
    monkeypatch.setattr(survey, "read_session", lambda: s2)
    dated = survey.survey_search(["bank.sop"], "complaint escalation matrix", top_docs=10, date_from="2026-01-01")
    assert "outside_scope" not in dated
    assert all(p["domains"] != ["bank", "bank.policy", "bank.governance"]
               for _, p in s2.queries_containing("passage_embedding"))


def test_cli_puts_the_outside_scope_next_first(monkeypatch):
    from click.testing import CliRunner

    from artmind import cli as cli_mod

    payload = {"domains": ["bank.sop"], "domain": "bank.sop", "query_type": "survey", "question": "q",
               "warnings": [], "documents": [{"doc_id": "X", "title": "x", "replaced": ["old"], "domain": "bank.sop"}],
               "topics": [], "outside_scope": [{"doc_id": "POL", "title": "p", "domain": "bank.policy", "relevance": 0.9}]}
    monkeypatch.setattr(survey, "survey_search", lambda *a, **k: payload)
    out = CliRunner().invoke(cli_mod.cli, ["query", "survey", "--domain", "bank.sop", "--shape", "brief", "--compact", "q"])
    import json
    nxt = json.loads(out.output)["next"]
    assert nxt.startswith("artmind query survey --shape brief --domain bank --compact q")
    assert "document-history" in nxt


def test_a_long_passage_is_clipped_at_a_word():
    text = "word " * 200
    clipped = scope_check._clip(text)
    assert len(clipped) <= scope_check.PASSAGE_CHARS + 2 and clipped.endswith(" …")
    assert scope_check._clip("short") == "short" and scope_check._clip(None) is None
