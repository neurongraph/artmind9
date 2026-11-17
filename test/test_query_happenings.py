"""`query happenings` (spec 2026-11-15 §5.2). Recorder: assert on the Cypher and params sent."""
import json

import pytest
from click.testing import CliRunner

from _neo4j_recorder import Recorder
from artmind import brief, graph_query as gq, hints
from artmind.cli import cli

SCHEMA = {"entity_types": {"EVENT": {"kind": "occurrent"}, "ACTIVITY": {"kind": "occurrent", "identity": "occasion"},
                           "PERSON": {"kind": "recurrent"}, "PLACE": {"kind": "recurrent"}}}
ROWS = [{"id": "a1", "name": "Tennis", "entity_class": "ACTIVITY", "type": "exercise", "date": "2024-03-30",
         "precision": "day", "time_of_day": None, "participants": ["Alex", "Alex"], "places": ["Club"],
         "chunk_ids": ["d1_000"], "doc_ids": ["d1"]},
        {"id": "a2", "name": "Tennis", "entity_class": "ACTIVITY", "type": "exercise", "date": "2025-04-02",
         "precision": "day", "time_of_day": None, "participants": [], "places": [], "chunk_ids": ["d2_000"],
         "doc_ids": ["d2"]}]


def _docs(n, date="2024-06-01"):
    return [{"doc_id": f"t{i}", "date": date, "domain": "pj"} for i in range(n)]


def _patch(monkeypatch, rows=ROWS, coverage=2, docs=None, total=None):
    monkeypatch.setattr("artmind.temporal.load_schema", lambda d: SCHEMA)

    def respond(cypher, params):
        if "entity_name_ft" in cypher:
            return [{"rows_total": len(rows) if total is None else total, "rows": rows}]
        if "passage_text_ft" in cypher:
            return [{"scanned": coverage, "docs": _docs(coverage) if docs is None else docs}]
        return []

    rec = Recorder(respond)
    monkeypatch.setattr(gq, "neo4j_session", lambda *a, **k: rec)
    return rec


def test_sends_occasion_classes_and_a_name_scoped_lucene_query(monkeypatch):
    rec = _patch(monkeypatch)
    gq.happenings(["pj"], "tennis", from_="2024-01-01", to="2025-12-31")
    (cypher, params), = rec.queries_containing("entity_name_ft")
    assert params["classes"] == ["ACTIVITY", "EVENT"]
    assert params["ft_query"] == "name:(tennis) OR aliases:(tennis)"
    assert params["term"] == "tennis" and "toLower(e.type) = toLower($term)" in cypher
    assert (params["from_"], params["to"]) == ("2024-01-01", "2025-12-31")
    assert "SAME_AS" in cypher and "_valid_precision" in cypher
    assert params["people"] == ["PERSON"] and params["places"] == ["PLACE"]
    assert params["limit"] == 200 and params["withId"] is None
    # an undated happening never falls inside a window
    assert "(NOT undated AND e._valid_from >= $from_)" in cypher
    (_, count_params), = rec.queries_containing("passage_text_ft")
    assert count_params["domains"] == ["pj"]


def test_entity_class_narrows_to_one_occasion_class(monkeypatch):
    rec = _patch(monkeypatch)
    gq.happenings(["pj"], "tennis", entity_class="activity")
    (_, params), = rec.queries_containing("entity_name_ft")
    assert params["classes"] == ["ACTIVITY"]
    with pytest.raises(ValueError, match="not an `identity: occasion` class"):
        gq.happenings(["pj"], "tennis", entity_class="PERSON")


def test_with_limits_to_happenings_linked_to_an_entity(monkeypatch):
    rec = _patch(monkeypatch)
    gq.happenings(["pj"], "tennis", with_id="p1")
    (cypher, params), = rec.queries_containing("entity_name_ft")
    assert params["withId"] == "p1" and "RELATES_TO" in cypher


def test_by_year_groups_rows(monkeypatch):
    _patch(monkeypatch)
    out = gq.happenings(["pj"], "tennis", by="year")
    assert [(p["period"], p["count"]) for p in out["periods"]] == [("2024", 1), ("2025", 1)]
    assert out["rows_total"] == 2 and out["truncated"] is False
    assert out["rows"][0]["participants"] == ["Alex"]  # deduplicated


def test_undated_rows_are_counted_not_grouped(monkeypatch):
    rows = [*ROWS, {**ROWS[0], "id": "a3", "date": None, "precision": "none", "doc_ids": ["d3"]}]
    _patch(monkeypatch, rows=rows, total=5, coverage=3)
    out = gq.happenings(["pj"], "tennis", by="month", limit=3)
    assert out["undated"] == 1
    assert [p["period"] for p in out["periods"]] == ["2024-03", "2025-04"]
    assert out["rows_total"] == 5 and out["truncated"] is True


def test_coverage_beyond_the_rows_suggests_a_survey(monkeypatch):
    _patch(monkeypatch, coverage=9)
    out = gq.happenings(["pj"], "tennis", by="year")
    assert out["coverage"] == {"matching_documents": 9, "documents_in_rows": 2}
    assert out["next"] == "artmind query survey tennis --domain pj --by year --shape brief --compact"


def test_coverage_counts_only_documents_in_the_window(monkeypatch):
    docs = [*_docs(2, "2024-06-01"), {"doc_id": "old", "date": "2019-01-01", "domain": "pj"},
            {"doc_id": "nodate", "date": None, "domain": "pj"}]
    _patch(monkeypatch, docs=docs)
    out = gq.happenings(["pj"], "lantern fair", from_="2024-01-01", to="2024-12-31")
    assert out["coverage"] == {"matching_documents": 2, "documents_in_rows": 2}
    assert out["next"] is None


def test_no_occasion_class_warns_and_runs_nothing(monkeypatch):
    rec = _patch(monkeypatch)
    monkeypatch.setattr("artmind.temporal.load_schema", lambda d: {"entity_types": {"PERSON": {"kind": "recurrent"}}})
    out = gq.happenings(["pj"], "tennis")
    assert out["rows"] == [] and out["warnings"] and not rec.queries_containing("entity_name_ft")


def test_survey_hint_carries_the_window():
    payload = {"coverage": {"matching_documents": 4, "documents_in_rows": 1}}
    assert hints.happenings_survey(payload, ["pj"], "lake trip", by="month", from_="2024-01-01",
                                   to="2024-12-31") == (
        "artmind query survey 'lake trip' --domain pj --by month --from 2024-01-01 --to 2024-12-31 "
        "--shape brief --compact")


def test_brief_row_caps_lists():
    row = brief.happening_row({**ROWS[0], "participants": list("abcdefg"), "places": list("wxyz"),
                               "chunk_ids": ["c1", "c2", "c3", "c4"]})
    assert (len(row["participants"]), len(row["places"]), len(row["chunk_ids"])) == (5, 3, 3)


def test_cli_envelope_and_brief_shape(monkeypatch):
    rec = _patch(monkeypatch)
    result = CliRunner().invoke(cli, ["query", "happenings", "--domain", "pj", "--match", "tennis",
                                      "--from", "2024", "--to", "2025-04", "--shape", "brief", "--compact"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["command"] == "query happenings" and payload["warnings"] == []
    assert set(payload["rows"][0]) == {"id", "name", "class", "date", "precision", "type", "participants",
                                       "places", "chunk_ids"}
    (_, params), = rec.queries_containing("entity_name_ft")
    assert (params["from_"], params["to"]) == ("2024-01-01", "2025-04-30")


def test_cli_rejects_a_bad_from(monkeypatch):
    _patch(monkeypatch)
    result = CliRunner().invoke(cli, ["query", "happenings", "--domain", "pj", "--match", "tennis",
                                      "--from", "last spring"])
    assert result.exit_code == 1 and "--from must be an ISO date" in result.output
