import json

from _neo4j_recorder import Recorder

from artmind import graph_query, survey, vector_query

DATES = {"A": "2023-02-01", "B": "2023-05-01", "C": "2023-07-01", "D": "2024-03-01", "E": "2024-04-01",
         "F": "2025-01-01", "U": None}


def _responder(hit_rows, order=("A", "B", "C", "D", "E", "F", "U")):
    def respond(cypher, params):
        if "passage_embedding" in cypher:
            return [{"row": {"kind": "passage", "id": f"{d}1_p01", "chunk_id": f"{d}1", "doc_id": d,
                             "text": f"text {d}", "cosine": 0.9 - i * 0.01}} for i, d in enumerate(order)]
        if "passage_text_ft" in cypher and "hit_limit" in params:
            return hit_rows
        if "passage_text_ft" in cypher:
            return []
        if "summary_embedding" in cypher or "summary_text_ft" in cypher:
            return []
        if "AS date" in cypher and "UNWIND $docIds" in cypher and "OPTIONAL MATCH" not in cypher:
            return [{"doc_id": d, "date": DATES.get(d)} for d in params["docIds"]]
        if "UNWIND $docIds" in cypher:
            return [{"doc_id": d, "title": f"Doc {d}", "name": f"{d}.md", "domain": "notes",
                     "summary": f"sum {d}", "date": DATES.get(d)} for d in params["docIds"]]
        if "count(d) AS total" in cypher:
            return [{"domain": "notes", "total": 7, "summarised": 7}]
        return []
    return respond


def _patch(monkeypatch, session):
    monkeypatch.setattr(vector_query, "embed_question", lambda q: [1.0, 0.0])
    monkeypatch.setattr(survey, "read_session", lambda: session)
    monkeypatch.setattr(graph_query, "expand_domain_family", lambda d: [d])


def test_best_two_per_year_in_chronological_order(monkeypatch):
    s = Recorder(_responder([{"date": "2023-02-01", "hits": 3}, {"date": "2024-03-01", "hits": 2},
                             {"date": "2025-01-01", "hits": 1}]))
    _patch(monkeypatch, s)
    out = survey.survey_search(["notes"], "alpha", by="year", per_period=2)
    assert out["by"] == "year"
    assert [(p["period"], [d["doc_id"] for d in p["documents"]]) for p in out["periods"]] == [
        ("2023", ["A", "B"]), ("2024", ["D", "E"]), ("2025", ["F"]), ("undated", ["U"])]
    assert [d["doc_id"] for d in out["documents"]] == ["A", "B", "D", "E", "F", "U"]
    assert out["periods"][0]["hits"] == 3
    assert out["documents"][0]["date"] == "2023-02-01"
    cypher, params = [c for c in s.calls if "hit_limit" in c[1]][0]
    assert "passage_text_ft" in cypher and "$domains" in cypher
    assert params["hit_limit"] == 5000 and params["domains"] == ["notes"]


def test_period_with_hits_but_no_document_is_listed_and_signposted(monkeypatch):
    _patch(monkeypatch, Recorder(_responder([{"date": "2021-06-15", "hits": 4}, {"date": "2023-02-01", "hits": 1}])))
    out = survey.survey_search(["notes"], "it's alpha", by="year")
    first = out["periods"][0]
    assert first == {"period": "2021", "hits": 4, "documents": []}
    assert out["next"] == "artmind query vector-text --domain notes --asOf 2021-12-31 --topK 10 --compact 'it'\"'\"'s alpha'"


def test_month_signpost_uses_last_day_of_month(monkeypatch):
    _patch(monkeypatch, Recorder(_responder([{"date": "2024-02-10", "hits": 2}])))
    out = survey.survey_search(["notes"], "alpha", by="month")
    assert [p["period"] for p in out["periods"]][:2] == ["2023-02", "2023-05"]
    empty = [p for p in out["periods"] if p["period"] == "2024-02"][0]
    assert empty["documents"] == [] and empty["hits"] == 2
    assert "--asOf 2024-02-29" in out["next"]


def test_cap_at_fifty_warns(monkeypatch):
    ids = [f"d{i:02d}" for i in range(60)]
    dates = {d: f"{2000 + i}-01-01" for i, d in enumerate(ids)}
    base = _responder([])

    def respond(cypher, params):
        if "AS date" in cypher and "UNWIND $docIds" in cypher and "OPTIONAL MATCH" not in cypher:
            return [{"doc_id": d, "date": dates[d]} for d in params["docIds"]]
        return base(cypher, params)

    _patch(monkeypatch, Recorder(respond))
    monkeypatch.setattr(survey.vector_query, "embed_question", lambda q: [1.0, 0.0])
    s = Recorder(lambda c, p: respond(c, p) if "passage_embedding" not in c else
                 [{"row": {"kind": "passage", "id": f"{d}_p", "chunk_id": f"{d}_c", "doc_id": d, "text": "t",
                           "cosine": 0.9}} for d in ids])
    monkeypatch.setattr(survey, "read_session", lambda: s)
    out = survey.survey_search(["notes"], "alpha", by="year", per_period=1)
    assert len(out["documents"]) == 50
    assert any("showing the best 50" in w for w in out["warnings"])


def test_without_by_payload_is_unchanged(monkeypatch):
    _patch(monkeypatch, Recorder(_responder([])))
    out = survey.survey_search(["notes"], "alpha", top_docs=3)
    assert list(out)[-5:] == ["query_type", "question", "parameters", "warnings", "documents", "topics"][-5:]
    assert set(out) - {"domain", "domains"} == {"query_type", "question", "parameters", "warnings", "documents", "topics"}
    assert "by" not in out and "periods" not in out and "next" not in out
    assert out["parameters"] == {"topDocs": 3, "passages": 1, "pool": 300, "legs": ["D", "C", "P"]}
    s = Recorder(_responder([]))
    _patch(monkeypatch, s)
    survey.survey_search(["notes"], "alpha", top_docs=3)
    assert not [c for c in s.calls if "hit_limit" in c[1]]


def test_cli_by_option(monkeypatch):
    from click.testing import CliRunner

    from artmind.cli import cli

    seen = {}

    def fake(domains, question, **kw):
        seen.update(kw)
        return {"query_type": "survey", "documents": [], "periods": [], "warnings": []}

    monkeypatch.setattr(survey, "survey_search", fake)
    monkeypatch.setattr("artmind.cli._parse_domains", lambda d: list(d))
    r = CliRunner().invoke(cli, ["query", "survey", "--domain", "notes", "--by", "month", "--perPeriod", "3", "q"])
    assert r.exit_code == 0, r.output
    assert seen["by"] == "month" and seen["per_period"] == 3
    json.loads(r.stdout)
