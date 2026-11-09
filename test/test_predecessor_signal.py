"""Task 4 (P2-2b): rows signal a superseded predecessor; next names document-history."""
import json
from unittest.mock import patch

from click.testing import CliRunner

from artmind import brief, hints, survey, vector_query
from artmind.cli import cli
from artmind.graph_query import supersedes_field

OLD = {"id": "o1", "name": "old.md", "path": "old.md", "valid_from": "2026-01-15"}


def test_supersedes_field_projects_valid_from():
    f = supersedes_field("d")
    assert "valid_from: coalesce(older._valid_from, older.valid_from)" in f
    assert "SUPERSEDES]->(older)" in f


def test_chunk_fetch_and_survey_cypher_carry_predecessors():
    assert "valid_from: coalesce(older._valid_from" in vector_query._chunk_fetch_cypher()
    assert "SUPERSEDES]->(older)" in survey._DOCUMENTS
    assert "AS supersedes" in survey._DOCUMENTS


def test_doc_ref_replaced_only_with_predecessors():
    ref = brief.doc_ref("d1", "T", "t.md", "dom", "2026-03-01", False, [OLD])
    assert ref["replaced"] == [{"name": "old.md", "valid_from": "2026-01-15"}]
    assert "replaced" not in brief.doc_ref("d1", "T", "t.md", "dom", "2026-03-01", False, [])
    assert "replaced" not in brief.doc_ref("d1", "T", "t.md", "dom", "2026-03-01", False)


def test_survey_row_carries_replaced():
    d = {"rank": 1, "doc_id": "d1", "title": "T", "domain": "dom", "date": None, "status": "current",
         "score": 0.1, "matched": [], "hits": {}, "gist": "g", "gist_source": "passage", "best": {},
         "passage": None, "replaced": [{"name": "old.md", "valid_from": "2026-01-15"}]}
    for kind in ("line", "gist", "passage"):
        assert brief.survey_row(kind, d)["replaced"] == d["replaced"]
    d["replaced"] = []
    assert "replaced" not in brief.survey_row("line", d)


def test_predecessor_next_picks_first_and_quotes():
    payload = {"rows": [
        {"doc": {"id": "a", "domain": "dom", "title": "A"}},
        {"doc": {"id": "b", "domain": "dom", "title": "B", "replaced": [{"name": "o", "valid_from": None}]}},
        {"doc": {"id": "c", "domain": "dom", "title": "C", "replaced": [{"name": "o2"}]}}]}
    assert hints.predecessor_next(payload) == "artmind query document-history --domain dom --documentName B --compact"
    full = {"rows": [{"document": {"_domain": "dom", "name": "my doc.md", "supersedes": [OLD]}}]}
    assert hints.predecessor_next(full) == (
        "artmind query document-history --domain dom --documentName 'my doc.md' --compact")
    assert hints.predecessor_next({"rows": [{"doc": {"id": "a", "domain": "dom"}}]}) is None


def _run(args, patch_target, result):
    with patch(patch_target, return_value=result):
        r = CliRunner().invoke(cli, args)
    assert r.exit_code == 0, r.output
    return json.loads(r.output)


def test_vector_text_next_names_document_history():
    result = {"rows": [{"doc": {"id": "b", "domain": "dom", "title": "B",
                                "replaced": [{"name": "old.md", "valid_from": "2026-01-15"}]}}], "warnings": []}
    out = _run(["query", "vector-text", "--domain", "dom", "--shape", "brief", "--compact", "q"],
               "artmind.vector_query.vector_text_search", result)
    assert out["next"] == "artmind query document-history --domain dom --documentName B --compact"


def test_vector_text_without_predecessors_keeps_next():
    result = {"rows": [{"doc": {"id": "b", "domain": "dom", "title": "B"}}], "warnings": []}
    out = _run(["query", "vector-text", "--domain", "dom", "--shape", "brief", "--compact", "q"],
               "artmind.vector_query.vector_text_search", result)
    assert out["next"] is None
    assert "replaced" not in json.dumps(out)


def test_survey_next_names_document_history():
    result = {"documents": [{"doc_id": "d1", "domain": "dom", "title": "T"},
                            {"doc_id": "d2", "domain": "dom", "title": "U",
                             "replaced": [{"name": "old.md", "valid_from": None}]}], "warnings": []}
    out = _run(["query", "survey", "--domain", "dom", "--compact", "q"], "artmind.survey.survey_search", result)
    assert out["next"] == "artmind query document-history --domain dom --documentName U --compact"


def test_predecessor_next_never_emits_raw_id():
    payload = {"rows": [{"doc": {"id": "01a15747-b8b6", "domain": "dom", "replaced": [{"name": "o"}]}}]}
    assert hints.predecessor_next(payload) is None
