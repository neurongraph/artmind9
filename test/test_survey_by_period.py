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
    assert out["periods"][0]["keyword_hits"] == 3 and out["periods"][0]["ranked"] == 3 and "hits" not in out["periods"][0]
    assert out["documents"][0]["date"] == "2023-02-01"
    # coverage with --by: the sum of the periods' keyword hits (3 + 2 + 1), what the full payload shows (6)
    assert out["coverage"] == {"matching_documents": 6, "shown": 6, "by": "year"}
    cypher, params = [c for c in s.calls if "hit_limit" in c[1]][0]
    assert "passage_text_ft" in cypher and "$domains" in cypher
    assert params["hit_limit"] == 5000 and params["domains"] == ["notes"]


def test_period_with_hits_but_no_document_is_listed_and_signposted(monkeypatch):
    _patch(monkeypatch, Recorder(_responder([{"date": "2021-06-15", "hits": 4}, {"date": "2023-02-01", "hits": 1}])))
    out = survey.survey_search(["notes"], "it's alpha", by="year")
    first = out["periods"][0]
    assert first == {"period": "2021", "keyword_hits": 4, "ranked": 0, "shown": 0, "documents": []}
    assert out["next"] == "artmind query graph timeline --domain notes --from 2021-01-01 --to 2021-12-31 --compact"


def test_month_signpost_uses_last_day_of_month(monkeypatch):
    _patch(monkeypatch, Recorder(_responder([{"date": "2024-02-10", "hits": 2}])))
    out = survey.survey_search(["notes"], "alpha", by="month")
    assert [p["period"] for p in out["periods"]][:2] == ["2023-02", "2023-05"]
    empty = [p for p in out["periods"] if p["period"] == "2024-02"][0]
    assert empty["documents"] == [] and empty["keyword_hits"] == 2
    assert out["next"] == "artmind query graph timeline --domain notes --from 2024-02-01 --to 2024-02-29 --compact"


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
    assert any("showing 50" in w for w in out["warnings"])


def test_without_by_payload_is_unchanged(monkeypatch):
    _patch(monkeypatch, Recorder(_responder([])))
    out = survey.survey_search(["notes"], "alpha", top_docs=3)
    assert set(out) - {"domain", "domains", "coverage"} == {"query_type", "question", "parameters", "warnings",
                                                            "documents"}
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


def _many(monkeypatch, n_docs, date_of, hit_rows=(), per_period=2):
    ids = [f"d{i:03d}" for i in range(n_docs)]
    dates = {d: date_of(i) for i, d in enumerate(ids)}

    def respond(cypher, params):
        if "passage_embedding" in cypher:
            return [{"row": {"kind": "passage", "id": f"{d}_p", "chunk_id": f"{d}_c", "doc_id": d, "text": "t",
                             "cosine": 0.99 - i * 0.001}} for i, d in enumerate(ids)]
        if "hit_limit" in params:
            return list(hit_rows)
        if "AS date" in cypher and "OPTIONAL MATCH" not in cypher and "UNWIND $docIds" in cypher:
            return [{"doc_id": d, "date": dates[d]} for d in params["docIds"]]
        if "UNWIND $docIds" in cypher:
            return [{"doc_id": d, "title": d, "name": d, "domain": "notes", "summary": None, "date": dates[d]}
                    for d in params["docIds"]]
        return []

    _patch(monkeypatch, Recorder(respond))
    return survey.survey_search(["notes"], "alpha", by="year", per_period=per_period)


def test_cap_keeps_the_top_document_of_every_period(monkeypatch):
    # 30 years x 3 docs; perPeriod 3 = 90 > 50: every year keeps its best, 20 extra by score.
    out = _many(monkeypatch, 90, lambda i: f"{1990 + i % 30}-01-01", per_period=3)
    assert len(out["documents"]) == 50
    assert [p["period"] for p in out["periods"]] == [str(y) for y in range(1990, 2020)]
    assert all(len(p["documents"]) >= 1 for p in out["periods"])
    warning = [w for w in out["warnings"] if "showing 50" in w][0]
    assert "periods that lost rows" in warning and "periods dropped" not in warning


def test_more_than_fifty_periods_keeps_those_with_most_hits(monkeypatch):
    hit_rows = [{"date": f"{1960 + i}-01-01", "hits": 100 if i < 5 else 1} for i in range(60)]
    out = _many(monkeypatch, 60, lambda i: f"{1960 + i}-01-01", hit_rows=hit_rows, per_period=1)
    assert len(out["documents"]) == 50
    kept = {p["period"] for p in out["periods"]}
    assert {str(1960 + i) for i in range(5)} <= kept
    warning = [w for w in out["warnings"] if "showing 50" in w][0]
    assert "periods dropped" in warning


def test_hit_limit_reached_warns_lower_bound(monkeypatch):
    monkeypatch.setattr(survey, "HIT_LIMIT", 5)
    out = _many(monkeypatch, 2, lambda i: "2023-01-01", hit_rows=[{"date": "2023-01-01", "hits": 5}])
    assert "keyword_hits are a lower bound (more than 5 matching passages)" in out["warnings"]
    assert out["coverage"]["at_least"] is True


def test_undated_empty_period_is_warned_not_signposted(monkeypatch):
    _patch(monkeypatch, Recorder(_responder([{"date": None, "hits": 3}], order=("A",))))
    out = survey.survey_search(["notes"], "alpha", by="year")
    und = [p for p in out["periods"] if p["period"] == "undated"][0]
    assert und["documents"] == [] and und["keyword_hits"] == 3
    assert out.get("next") is None
    assert any("undated" in w for w in out["warnings"])


def test_next_is_joined_not_overwritten(monkeypatch):
    _patch(monkeypatch, Recorder(_responder([{"date": "2021-06-15", "hits": 4}])))
    out = survey.survey_search(["notes"], "alpha", by="year")
    from artmind import hints
    assert hints.join_next("artmind query page h --from 3", out["next"]).startswith("artmind query page h --from 3; artmind query graph timeline")


def test_partial_period_is_flagged_with_a_next_into_that_period(monkeypatch):
    _patch(monkeypatch, Recorder(_responder([{"date": "2023-02-01", "hits": 2}, {"date": "2024-03-01", "hits": 2},
                                             {"date": "2025-01-01", "hits": 21}])))
    out = survey.survey_search(["notes"], "what's alpha", by="year", per_period=2)
    p = {x["period"]: x for x in out["periods"]}
    assert p["2025"]["shown"] == 1 and p["2025"]["partial"] is True
    assert p["2023"]["shown"] == 2 and "partial" not in p["2023"]   # 2 hits, 2 shown
    assert "partial" not in p["2024"]
    assert out["complete"] is False and out["partial_periods"] == ["2025"]
    assert out["next"] == ("artmind query survey --domain notes --from 2025-01-01 --to 2025-12-31 "
                           "--shape brief --compact 'what'\"'\"'s alpha'")


def test_partial_next_comes_before_the_empty_period_next_and_picks_the_widest_gap(monkeypatch):
    _patch(monkeypatch, Recorder(_responder([{"date": "2021-06-15", "hits": 4}, {"date": "2023-02-01", "hits": 9},
                                             {"date": "2024-03-01", "hits": 30}])))
    out = survey.survey_search(["notes", "other"], "alpha", by="year")
    assert out["partial_periods"] == ["2023", "2024"]
    first, second = out["next"].split("; ")
    assert first == ("artmind query survey --domain notes --domain other --from 2024-01-01 --to 2024-12-31 "
                     "--shape brief --compact alpha")
    assert second.startswith("artmind query graph timeline") and "2021-01-01" in second


def test_month_partial_uses_month_bounds(monkeypatch):
    _patch(monkeypatch, Recorder(_responder([{"date": "2023-02-01", "hits": 5}])))
    out = survey.survey_search(["notes"], "alpha", by="month")
    assert out["partial_periods"] == ["2023-02"]
    assert "--from 2023-02-01 --to 2023-02-28 " in out["next"]


def test_no_partial_period_adds_no_keys(monkeypatch):
    _patch(monkeypatch, Recorder(_responder([{"date": "2023-02-01", "hits": 2}, {"date": "2025-01-01", "hits": 1}])))
    out = survey.survey_search(["notes"], "alpha", by="year", per_period=2)
    assert "complete" not in out and "partial_periods" not in out and out.get("next") is None
    assert all("partial" not in p for p in out["periods"])
    assert all("shown" in p for p in out["periods"])


def test_window_filters_every_leg_on_the_coalesced_date(monkeypatch):
    s = Recorder(_responder([]))
    _patch(monkeypatch, s)
    survey.survey_search(["notes"], "alpha", date_from="2025-01-01", date_to="2025-12-31")
    legs = [(c, p) for c, p in s.calls if "passage_embedding" in c or "passage_text_ft" in c
            or "summary_embedding" in c or "summary_text_ft" in c]
    assert len(legs) == 6   # P vec+text, D vec+text, C vec+text
    for cypher, params in legs:
        assert "coalesce(d._valid_from, d.valid_from)" in cypher and "$date_from" in cypher and "$date_to" in cypher
        assert params["date_from"] == "2025-01-01" and params["date_to"] == "2025-12-31"


def test_without_a_window_the_leg_queries_are_unchanged(monkeypatch):
    s = Recorder(_responder([]))
    _patch(monkeypatch, s)
    survey.survey_search(["notes"], "alpha")
    for cypher, params in s.calls:
        assert "date_from" not in cypher and "date_from" not in params


def test_window_is_validated():
    import pytest

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        survey.survey_search(["notes"], "q", date_from="2025")
    with pytest.raises(ValueError, match="before"):
        survey.survey_search(["notes"], "q", date_from="2025-02-01", date_to="2025-01-01")


def test_cli_from_to_options(monkeypatch):
    from click.testing import CliRunner

    from artmind.cli import cli

    seen = {}

    def fake(domains, question, **kw):
        seen.update(kw)
        return {"query_type": "survey", "documents": [], "warnings": []}

    monkeypatch.setattr(survey, "survey_search", fake)
    monkeypatch.setattr("artmind.cli._parse_domains", lambda d: list(d))
    r = CliRunner().invoke(cli, ["query", "survey", "--domain", "notes", "--from", "2025-01-01",
                                 "--to", "2025-12-31", "q"])
    assert r.exit_code == 0, r.output
    assert seen["date_from"] == "2025-01-01" and seen["date_to"] == "2025-12-31"
