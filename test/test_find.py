"""artmind/find.py and `query find` (spec 2026-11-12 find in a survey's documents). Synthetic passages only."""
import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from _neo4j_recorder import Recorder
from artmind import find, graph_query, vector_query
from artmind.cli import cli

STORED = {"command": "query survey", "query_type": "survey", "question": "how are complaints handled",
          "domains": ["bank"], "documents": [{"doc_id": "D1"}, {"doc_id": "D2"}]}


def _passage(pid, doc, text, emb=(1.0, 0.0)):
    return {"id": pid, "chunk_id": pid.rsplit("_p", 1)[0], "doc_id": doc, "text": text,
            "embedding": list(emb) if emb is not None else None}


PASSAGES = [
    _passage("D1_001_p1", "D1", "Complaints are acknowledged quickly by the team.", (0.99, 0.1)),
    _passage("D1_002_p1", "D1", "Escalate under COM-POL-006 within 8 weeks of the complaint.", (0.2, 0.98)),
    _passage("D1_003_p1", "D1", "General introduction to the policy.", (0.9, 0.3)),
    _passage("D1_004_p1", "D1", "Complaint records are kept for audit.", (0.8, 0.5)),
    _passage("D2_001_p1", "D2", "Branch opening hours.", (0.7, 0.7)),
]


def _respond(passages=PASSAGES):
    def respond(cypher, params):
        if "DocPassage" in cypher:
            return [p for p in passages if p["doc_id"] in params["docIds"]]
        if "UNWIND $docIds" in cypher:
            return [{"doc_id": d, "title": f"Doc {d}", "name": f"{d}.md", "domain": "bank", "date": "2024-01-01",
                     "superseded": False, "supersedes": []} for d in params["docIds"]]
        return []
    return respond


@pytest.fixture
def session(monkeypatch):
    s = Recorder(_respond())
    monkeypatch.setattr(graph_query, "expand_domain_family", lambda d: [d])
    return s


def _run(session, part, **kw):
    return find.find(STORED, "abc123abc123", part, session_factory=lambda: session,
                     embed=lambda q: [1.0, 0.0], **kw)


def test_the_read_is_scoped_to_the_surveys_documents_and_domains(session):
    _run(session, "complaint escalation")
    (cypher, params), = session.queries_containing("DocPassage")[:1]
    assert params["docIds"] == ["D1", "D2"] and params["domains"] == ["bank"]
    assert "DocPassageHistory" in cypher


def test_a_passage_with_the_parts_code_ranks_above_one_closer_by_meaning(session):
    out = _run(session, "escalation deadline COM-POL-006")
    assert out["rows"][0]["chunk_id"] == "D1_002"
    assert "COM-POL-006" in out["rows"][0]["matched"]
    assert out["scope"] == {"documents": 2, "passages": 5}


def test_per_doc_and_top_hold(session):
    out = _run(session, "complaint", per_doc=2, top=3)
    docs = [r["doc"]["id"] for r in out["rows"]]
    assert len(out["rows"]) == 3 and docs.count("D1") == 2


def test_a_passage_without_embedding_still_ranks_by_keyword(monkeypatch):
    rows = [_passage("D1_009_p1", "D1", "The complaint deadline is 8 weeks.", None),
            _passage("D2_001_p1", "D2", "Branch opening hours.", (1.0, 0.0))]
    s = Recorder(_respond(rows))
    monkeypatch.setattr(graph_query, "expand_domain_family", lambda d: [d])
    out = _run(s, "complaint deadline of 8 weeks")
    assert out["rows"][0]["chunk_id"] == "D1_009" and out["rows"][0]["relevance"] is None
    assert any("no embedding" in w for w in out["warnings"])


def test_no_word_match_warns_and_points_at_a_new_survey(session):
    out = _run(session, "mortgage")
    assert out["rows"] and all(not r["matched"] for r in out["rows"])
    assert any("meaning only" in w for w in out["warnings"])
    assert out["next"] == "artmind query survey --domain bank --shape brief mortgage"


def test_a_non_survey_handle_is_refused():
    with pytest.raises(ValueError, match="run query survey"):
        find.survey_scope({"command": "query vector-text", "rows": []})


def test_facts_normalise_codes_amounts_and_numbers():
    assert find.facts("COM-POL-006 costs £1,250 or 2.5 % within 8 weeks") == ["com-pol-006", "£1250", "2.5%", "8weeks"]


def test_cli_reads_the_handle_and_runs_find(monkeypatch, tmp_path, session):
    from artmind import cli as cli_mod, result_store

    monkeypatch.setattr(cli_mod, "LOGS_DIR", tmp_path)
    monkeypatch.setattr(graph_query, "read_session", lambda: session)
    monkeypatch.setattr(vector_query, "embed_question", lambda q: [1.0, 0.0])
    monkeypatch.delenv("ARTMIND_OUTPUT_BUDGET", raising=False)
    handle = result_store.store(tmp_path / "results", STORED, json.dumps)
    result = CliRunner().invoke(cli, ["query", "find", handle, "COM-POL-006", "--compact"])
    assert result.exit_code == 0, result.output
    out = json.loads(result.output)
    assert out["command"] == "query find" and out["rows"][0]["chunk_id"] == "D1_002"


def test_cli_unknown_handle_is_a_click_error(monkeypatch, tmp_path):
    from artmind import cli as cli_mod

    monkeypatch.setattr(cli_mod, "LOGS_DIR", tmp_path)
    result = CliRunner().invoke(cli, ["query", "find", "abcdefabcdef", "x"])
    assert result.exit_code == 1 and "re-run" in result.output


def test_cli_bounds_top_and_per_doc():
    assert CliRunner().invoke(cli, ["query", "find", "abcdefabcdef", "x", "--top", "21"]).exit_code == 2
    assert CliRunner().invoke(cli, ["query", "find", "abcdefabcdef", "x", "--perDoc", "6"]).exit_code == 2
