"""`--shape brief` (spec 2026-11-05 bounded-output §4): scorer, allocation and the
rows every command emits. Synthetic text only."""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from _neo4j_recorder import Recorder
from test_query_layer import _RecordingSession, _hub_responses, _install_session

from artmind import brief, graph_query, survey, vector_query
from artmind import graph_query as gq
from artmind.cli import cli


# --- scorer -------------------------------------------------------------------


def _gist(question, *docs):
    return brief.build_gists(question, [brief.GistInput(d, t, d) for d, t in docs])


def test_gist_picks_the_sentence_matching_the_question_verbatim():
    out = _gist("what is the notice period for termination",
                ("A", [("passage", "The parties meet yearly. Notice of termination is ninety days. Fees apply."),
                       ("document_summary", "A services agreement.")]))
    a = out["A"]
    assert a["gist"] == "Notice of termination is ninety days."
    assert a["gist_source"] == "passage"
    assert set(a["matched"]) == {"notice", "termination"}


def test_plural_fold_matches_and_ties_go_to_the_earlier_source():
    out = _gist("which contracts renew", ("A", [("chunk_summary", "Contract renews in May."),
                                                ("document_summary", "Contract renews in May.")]))
    assert out["A"]["gist_source"] == "chunk_summary"
    assert "contracts" in out["A"]["matched"]


def test_idf_prefers_the_rarer_term():
    docs = [("A", [("passage", "Penalty clause applies. Warranty lasts a year.")]),
            ("B", [("passage", "Warranty lasts two years.")]),
            ("C", [("passage", "Warranty is standard.")])]
    out = _gist("penalty warranty", *docs)
    assert out["A"]["gist"] == "Penalty clause applies."


def test_a_shared_number_or_name_scores():
    out = _gist("when did Acme pay 2019", ("A", [("passage", "The weather was fine. Acme paid in 2019.")]))
    assert out["A"]["gist"] == "Acme paid in 2019."


def test_fallback_is_the_first_document_summary_sentence_with_no_matches():
    out = _gist("zebra migration", ("A", [("passage", "Alpha beta gamma."),
                                           ("document_summary", "A lease for an office. It has five pages.")]))
    assert out["A"] == {"gist": "A lease for an office.", "gist_source": "document_summary", "matched": []}


def test_long_gist_is_cut_at_a_sentence_or_word_boundary():
    long = "Termination notice " + "word " * 80
    gist = _gist("termination", ("A", [("passage", long)]))["A"]["gist"]
    assert len(gist) <= brief.GIST_CHARS and not gist.endswith("wor")


# --- allocation -----------------------------------------------------------------


def _cands(n, rels=None, hits=None, line=100, gist=300, passage=1500):
    rels = rels or {}
    return [brief.Candidate(f"d{i}", i, 1.0 / i, (hits or {}).get(i, 0), rels.get(i, 0.5 - 0.05 * i), True, line, gist, passage)
            for i in range(1, n + 1)]


def test_every_document_is_listed_and_floors_are_met():
    kinds = brief.allocate(_cands(20), passage_docs=3, gist_docs=10, budget=None)
    assert len(kinds) == 20
    assert sum(k == "passage" for k in kinds.values()) >= 3
    assert sum(k == "gist" for k in kinds.values()) == 10
    assert sum(k == "line" for k in kinds.values()) == 20 - 10 - sum(k == "passage" for k in kinds.values())


def test_needle_within_003_of_top_relevance_gets_a_passage_row():
    rels = {1: 0.60, 2: 0.55, 3: 0.50, 4: 0.50, 7: 0.78, 9: 0.76, 10: 0.74}
    kinds = brief.allocate(_cands(12, rels=rels), passage_docs=2, gist_docs=0, budget=None)
    # both passage slots go to the needles (ranked 7 and 9), not to ranks 1 and 2
    assert {d for d, k in kinds.items() if k == "passage"} == {"d7", "d9"}
    assert kinds["d10"] == "line"


def test_flat_relevance_needle_set_is_capped_by_passage_slots():
    rels = {i: 0.88 - 0.001 * i for i in range(1, 21)}  # every document within 0.03 of the top
    kinds = brief.allocate(_cands(20, rels=rels), passage_docs=3, gist_docs=0, budget=None)
    assert {d for d, k in kinds.items() if k == "passage"} == {"d1", "d2", "d3"}


def test_no_passage_slots_means_no_passage_rows():
    rels = {7: 0.9, 9: 0.89}
    kinds = brief.allocate(_cands(12, rels=rels), passage_docs=0, gist_docs=0, budget=None)
    assert all(k == "line" for k in kinds.values())


def test_budget_is_never_exceeded_and_every_document_stays_listed():
    cands = _cands(50)
    for budget in (6000, 9000, 16000):
        kinds = brief.allocate(cands, passage_docs=3, gist_docs=10, budget=budget)
        assert len(kinds) == 50
        sizes = {"line": 100, "gist": 300, "passage": 1500}
        assert 1000 + sum(sizes[k] for k in kinds.values()) <= budget


def test_hits_break_gist_ties():
    c = _cands(6, rels={i: 0.1 * (7 - i) for i in range(1, 7)})
    for x in c:
        x.score = 1.0
    c[4].hits = 9
    kinds = brief.allocate(c, passage_docs=0, gist_docs=1, budget=None)
    assert kinds["d5"] == "gist"


def test_budget_fills_gists_beyond_the_floor_and_stops():
    unlimited = brief.allocate(_cands(30), passage_docs=1, gist_docs=2, budget=None)
    assert sum(k == "gist" for k in unlimited.values()) == 2
    budgeted = brief.allocate(_cands(30), passage_docs=1, gist_docs=2, budget=6000)
    gists = sum(k == "gist" for k in budgeted.values())
    assert 2 < gists < 29
    cost = 1000 + 1500 + 300 * gists + 100 * (30 - 1 - gists)
    assert cost <= 6000


def test_budget_env():
    assert brief.output_budget({"ARTMIND_OUTPUT_BUDGET": "16000"}) == 16000
    assert brief.output_budget({"ARTMIND_OUTPUT_BUDGET": "0"}) is None
    assert brief.output_budget({}) is None
    with pytest.raises(ValueError, match="ARTMIND_OUTPUT_BUDGET"):
        brief.output_budget({"ARTMIND_OUTPUT_BUDGET": "x"})


# --- survey ---------------------------------------------------------------------


def _survey_respond(n=12):
    def respond(cypher, params):
        if "passage_embedding" in cypher:
            return [{"row": {"kind": "passage", "id": f"c{i}_p1", "chunk_id": f"c{i}", "doc_id": f"D{i}",
                             "text": f"Passage {i} about termination notice. Other filler words here.",
                             "cosine": 0.9 - 0.01 * i if i != 8 else 0.95}} for i in range(1, n + 1)]
        if "summary_embedding" in cypher:
            if params["level"] == "document":
                return [{"row": {"kind": "summary", "id": f"D{i}:summary", "doc_id": f"D{i}", "chunk_id": None,
                                 "text": f"Agreement number {i}. It governs services.", "cosine": 0.5}}
                        for i in range(1, n + 1)]
            return []
        if "UNWIND $docIds" in cypher:
            return [{"doc_id": d, "title": f"Doc {d}", "name": f"{d}.md", "domain": "notes",
                     "summary": f"Agreement number {d}. It governs services.", "date": "2018-06-01",
                     "superseded": d == "D2"} for d in params["docIds"]]
        return []
    return respond


def _patch(monkeypatch, session):
    monkeypatch.setattr(vector_query, "embed_question", lambda q: [1.0, 0.0])
    monkeypatch.setattr(survey, "read_session", lambda: session)
    monkeypatch.setattr(vector_query, "read_session", lambda: session)
    monkeypatch.setattr(graph_query, "expand_domain_family", lambda d: [d])


def test_survey_brief_lists_every_document_in_three_row_kinds(monkeypatch):
    monkeypatch.delenv("ARTMIND_OUTPUT_BUDGET", raising=False)
    _patch(monkeypatch, Recorder(_survey_respond()))
    out = survey.survey_search(["notes"], "termination notice", top_docs=12, shape="brief",
                               passage_docs=2, gist_docs=4)
    docs = out["documents"]
    assert len(docs) == 12
    assert {"rank", "doc_id", "title", "date", "status", "score", "matched", "hits"} <= set(docs[0])
    gist_rows = [d for d in docs if "gist" in d]
    passage_rows = [d for d in docs if "passage" in d]
    assert len(gist_rows) >= 4 and len(passage_rows) >= 2
    for d in gist_rows:
        assert {"domain", "gist", "gist_source", "best"} <= set(d) and len(d["gist"]) <= 220
    assert any(d["doc_id"] == "D8" for d in passage_rows)
    assert all(set(d["passage"]) == {"passage_id", "chunk_id", "text", "relevance"} for d in passage_rows)
    assert next(d for d in docs if d["doc_id"] == "D2")["status"] == "superseded"
    assert docs[0]["date"] == "2018-06-01"
    assert out["parameters"]["shape"] == "brief"


def test_survey_brief_respects_the_output_budget(monkeypatch):
    _patch(monkeypatch, Recorder(_survey_respond(12)))
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "16000")
    out = survey.survey_search(["notes"], "termination notice", top_docs=12, shape="brief", passage_docs=0, gist_docs=0)
    assert sum("gist" in d for d in out["documents"]) == 12     # passage rows carry the gist too
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "2500")
    tight = survey.survey_search(["notes"], "termination notice", top_docs=12, shape="brief", passage_docs=0, gist_docs=0)
    assert sum("gist" in d for d in tight["documents"]) < 11


def test_survey_full_rows_gain_title_date_status_and_keep_keys(monkeypatch):
    s = Recorder(_survey_respond(3))
    _patch(monkeypatch, s)
    out = survey.survey_search(["notes"], "termination", top_docs=3)
    d = out["documents"][0]
    assert {"summary", "ranks", "passages", "chunk_summary", "name", "title"} <= set(d)
    assert d["date"] == "2018-06-01" and d["status"] == "current"
    cypher = s.queries_containing("UNWIND $docIds")[0][0]
    assert "d.valid_from AS date" in cypher and "SUPERSEDES" in cypher


def test_survey_expand_returns_passage_rows_without_reranking(monkeypatch):
    _patch(monkeypatch, Recorder(_survey_respond()))
    out = survey.survey_search(["notes"], "termination notice", top_docs=12, shape="brief", expand=["D5", "D3"])
    assert [p["doc"]["id"] for p in out["passages"]] == ["D5", "D3"]
    assert set(out["passages"][0]) == {"rank", "passage_id", "chunk_id", "text", "relevance", "doc"}
    assert set(out["passages"][0]["doc"]) == {"id", "title", "domain", "date", "status"}


def test_survey_default_depth_by_shape(monkeypatch):
    monkeypatch.delenv("ARTMIND_SURVEY_TOP_DOCS", raising=False)
    payload = {"query_type": "survey", "documents": [], "topics": [], "warnings": []}
    seen = []
    for shape in ("full", "brief"):
        with patch("artmind.survey.survey_search", return_value=payload) as search:
            r = CliRunner().invoke(cli, ["query", "survey", "--domain", "notes", "--shape", shape, "--compact", "q"])
            assert r.exit_code == 0, r.output
            seen.append(search.call_args[1]["top_docs"])
    assert seen == [10, 50]
    monkeypatch.setenv("ARTMIND_SURVEY_TOP_DOCS", "7")
    for shape in ("full", "brief"):
        with patch("artmind.survey.survey_search", return_value=payload) as search:
            CliRunner().invoke(cli, ["query", "survey", "--domain", "notes", "--shape", shape, "--compact", "q"])
            assert search.call_args[1]["top_docs"] == 7


def test_topic_gather_passes_its_own_depth():
    from artmind.topics import gather
    calls = []
    gather.gather("t", ["notes"], {"kind": "topic"}, 12,
                  search=lambda d, t, **kw: calls.append(kw) or {"documents": []}, session_factory=lambda: Recorder())
    assert calls[0]["top_docs"] == 12 and "shape" not in calls[0]


# --- vector-text ----------------------------------------------------------------

VT_VEC = [{"row": {"kind": "passage", "id": "c1_p1", "chunk_id": "c1", "doc_id": "d1", "char_start": 0,
                   "char_end": 9, "text": "x" * 1500, "cosine": 0.9}},
          {"row": {"kind": "passage", "id": "c1_p2", "chunk_id": "c1", "doc_id": "d1", "char_start": 9,
                   "char_end": 20, "text": "second passage. " * 60, "cosine": 0.8}},
          {"row": {"kind": "passage", "id": "c2_p1", "chunk_id": "c2", "doc_id": "d2", "char_start": 0,
                   "char_end": 9, "text": "lonely", "cosine": 0.7}}]
VT_CHUNKS = [
    {"chunk": {"id": "c1", "doc_id": "d1", "text": "whole chunk " * 500},
     "document": {"id": "d1", "name": "d1.pdf", "_domain": "notes", "title": "Doc One", "date": "2020-01-01",
                  "superseded": False}},
    {"chunk": {"id": "c2", "doc_id": "d2", "text": "whole two"},
     "document": {"id": "d2", "name": "d2.pdf", "_domain": "notes", "title": "d2.pdf", "date": None,
                  "superseded": True}},
]


def _vt_respond(cypher, params):
    if "VECTOR INDEX passage_embedding" in cypher:
        return VT_VEC
    if "UNWIND $chunkIds AS cid" in cypher:
        return [c for c in VT_CHUNKS if c["chunk"]["id"] in params["chunkIds"]]
    return []


def test_vector_text_brief_rows(monkeypatch):
    s = Recorder(_vt_respond)
    _patch(monkeypatch, s)
    out = vector_query.vector_text_search(["notes"], "q", 5, shape="brief")
    assert out["parameters"]["context"] == "passage"
    r1, r2 = out["rows"]
    assert {"rank", "passage_id", "chunk_id", "text", "relevance", "doc"} <= set(r1)
    assert len(r1["text"]) <= 1200
    assert r1["doc"] == {"id": "d1", "title": "Doc One", "domain": "notes", "date": "2020-01-01", "status": "current"}
    assert r1["passages_matched"] == 2 and 0 < len(r1["second_passage"]) <= 400
    assert r1["second_passage"].startswith("second passage.")
    assert "second_passage" not in r2 and r2["doc"]["status"] == "superseded" and r2["doc"]["date"] is None
    cypher = s.queries_containing("UNWIND $chunkIds AS cid")[0][0]
    assert "SUPERSEDES" in cypher and "valid_from" in cypher


def test_vector_text_full_keeps_keys_and_document_gains_fields(monkeypatch):
    _patch(monkeypatch, Recorder(_vt_respond))
    out = vector_query.vector_text_search(["notes"], "q", 5)
    row = out["rows"][0]
    assert {"chunk", "document", "source_type", "passage", "passages_matched", "score"} <= set(row)
    assert row["document"]["title"] == "Doc One" and row["document"]["date"] == "2020-01-01"
    assert row["document"]["status"] == "current" and "superseded" not in row["document"]
    assert out["rows"][1]["document"]["status"] == "superseded"


# --- chunks and entity-context ------------------------------------------------------


def test_chunks_brief_and_full(monkeypatch):
    rows = [{"chunk": {"id": "c1", "doc_id": "d1", "_domain": "notes", "text": "t"},
             "document": {"id": "d1", "name": "d1.md", "_domain": "notes", "title": "d1.md",
                          "date": "2021-02-03", "superseded": True}}]
    seen = {}

    def fake(cypher, params):
        seen["cypher"], seen["params"] = cypher, params
        return [json.loads(json.dumps(r)) for r in rows]

    monkeypatch.setattr(gq, "_run_read_query", fake)
    brief_out = gq.chunks_by_id(["notes"], ["c1"], shape="brief")
    assert brief_out["rows"][0]["doc"] == {"id": "d1", "title": "d1.md", "domain": "notes", "date": "2021-02-03",
                                           "status": "superseded"}
    assert brief_out["rows"][0]["chunk"]["text"] == "t"
    assert seen["params"]["chunkIds"] == ["c1"] and "SUPERSEDES" in seen["cypher"]
    full = gq.chunks_by_id(["notes"], ["c1"])
    assert full["rows"][0]["document"]["status"] == "superseded"
    assert "superseded" not in full["rows"][0]["document"] and full["rows"][0]["document"]["title"] == "d1.md"


def test_entity_context_brief_shortens_only_chunk_rows(monkeypatch):
    session = _RecordingSession(_hub_responses())
    _install_session(monkeypatch, session)
    full = gq.entity_context(["fiction"], "e1", include_chunks=2)
    session2 = _RecordingSession(_hub_responses())
    _install_session(monkeypatch, session2)
    brief_out = gq.entity_context(["fiction"], "e1", include_chunks=2, shape="brief")
    f, b = full["rows"][0], brief_out["rows"][0]
    assert f["entityData"] == b["entityData"] and f["connections"] == b["connections"]
    assert {"id", "text", "document"} <= set(f["chunks"][0])
    assert {"rank", "chunk_id", "text", "relevance", "doc"} <= set(b["chunks"][0])
    assert set(b["chunks"][0]["doc"]) == {"id", "title", "domain", "date", "status"}
    assert b["chunks"][0]["doc"]["status"] == "current"


@pytest.mark.parametrize("args,target,ret", [
    (["survey", "--domain", "notes", "--shape", "brief", "--passageDocs", "2", "--gistDocs", "5",
      "--expand", "A,B", "q"], "artmind.survey.survey_search", {"documents": []}),
    (["vector-text", "--domain", "notes", "--shape", "brief", "q"], "artmind.vector_query.vector_text_search", {"rows": []}),
    (["chunks", "--domain", "notes", "--idList", "c1", "--shape", "brief"], "artmind.graph_query.chunks_by_id", {"rows": []}),
    (["entity-context", "--domain", "notes", "--entityId", "e1", "--shape", "brief"],
     "artmind.graph_query.entity_context", {"rows": []}),
])
def test_every_command_passes_shape(args, target, ret):
    with patch(target, return_value=ret) as fn:
        r = CliRunner().invoke(cli, ["query", *args, "--compact"])
    assert r.exit_code == 0, r.output
    assert fn.call_args[1]["shape"] == "brief"
    if args[0] == "survey":
        assert fn.call_args[1]["passage_docs"] == 2 and fn.call_args[1]["gist_docs"] == 5
        assert fn.call_args[1]["expand"] == ["A", "B"]


def test_bad_shape_is_a_usage_error():
    r = CliRunner().invoke(cli, ["query", "chunks", "--domain", "n", "--idList", "c", "--shape", "tiny"])
    assert r.exit_code == 2
