"""`--shape brief` for the graph commands (round 8 P58/P59; spec 2026-11-05 bounded-output §4):
pattern1, pattern2, pattern7, entity-context's connections, and `query insights show`.

Synthetic data only; Neo4j is a recorded session or a patched `_run_read_query`."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind import brief
from artmind import graph_query as gq
from artmind.cli import cli
from artmind.insights.model import Finding
from test_query_layer import _RecordingSession, _hub_responses, _install_session

LONG = ("Alpha Corp is a synthetic supplier of widgets. It also sells gadgets and a long tail of other "
        "things that no brief row should carry in full because they only inflate the answer.")
RUN_ONE_SENTENCE = "word " * 80  # no sentence end: cut at a word boundary


def _entity(i: int = 1, description=LONG) -> dict:
    return {"_id": f"e{i}", "name": f"Entity {i}", "entity_class": "ORG", "description": description,
            "label": ["Entity", "ORG"], "aliases": ["x"] * 40, "recent_activity": [f"a{n}" for n in range(30)]}


def _invoke(args, monkeypatch=None):
    r = CliRunner().invoke(cli, args)
    assert r.exit_code == 0, r.output
    return json.loads(r.stdout)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("ARTMIND_OUTPUT_BUDGET", raising=False)
    monkeypatch.setenv("ARTMIND_NO_PROXY", "1")


# --- the one-line cut ------------------------------------------------------------------


def test_one_line_is_the_first_sentence():
    assert brief.one_line(LONG) == "Alpha Corp is a synthetic supplier of widgets."


def test_one_line_cuts_a_long_run_at_a_word_boundary():
    out = brief.one_line(RUN_ONE_SENTENCE)
    assert out and len(out) <= brief.ONE_LINE_CHARS and not out.endswith(" ") and out.split()[-1] == "word"


def test_one_line_empty_is_none():
    assert brief.one_line(None) is None and brief.one_line("  ") is None


# --- patterns ----------------------------------------------------------------------------


def _run_pattern(monkeypatch, args, rows):
    monkeypatch.setattr(gq, "_run_read_query", lambda cypher, params: json.loads(json.dumps(rows)))
    return _invoke(["query", "graph", *args, "--domain", "d", "--compact"])


def test_pattern1_brief_rows_and_totals(monkeypatch):
    rows = [{"entityData": _entity(i), "_rowsTotal": 40} for i in range(1, 4)]
    full = _run_pattern(monkeypatch, ["pattern1", "--entityClass", "ORG"], rows)
    out = _run_pattern(monkeypatch, ["pattern1", "--entityClass", "ORG", "--shape", "brief"], rows)
    assert out["rows"][0] == {"id": "e1", "name": "Entity 1", "class": "ORG",
                              "description": "Alpha Corp is a synthetic supplier of widgets."}
    assert out["rows_total"] == full["rows_total"] == 40 and out["truncated"] is True
    assert out["parameters"]["shape"] == "brief" and "shape" not in full["parameters"]
    assert "--shape full" in out["next"] and "entity-context" in out["next"] and "--entityId e1" in out["next"]
    assert len(json.dumps(out)) < len(json.dumps(full))
    assert "entityData" in full["rows"][0]  # full is unchanged


def test_pattern2_brief_keeps_evidence_ids_and_totals(monkeypatch):
    chunks = [{"id": f"c{i}", "name": "n", "doc_id": "d1", "source_type": "document"} for i in range(30)]
    docs = [{"id": f"d{i}", "name": f"d{i}.md", "path": "/p", "source_path": "/s"} for i in range(30)]
    rows = [{"entityData": _entity(), "doc_sources": chunks, "source_documents": docs}]
    out = _run_pattern(monkeypatch, ["pattern2", "--entityIdList", "e1", "--maxDocSources", "25",
                                     "--maxSourceDocuments", "25", "--shape", "brief"], rows)
    row = out["rows"][0]
    assert set(row) == {"id", "name", "class", "description", "chunk_ids", "chunks_total", "documents",
                        "documents_total"}
    assert row["chunk_ids"] == [f"c{i}" for i in range(brief.EVIDENCE_IDS)]
    assert row["chunks_total"] == 30 and row["documents_total"] == 30
    assert row["documents"][0] == {"id": "d0", "title": "d0", "date": None}
    assert "aliases" not in row and "recent_activity" not in row


def test_pattern7_brief_keeps_documents_and_next(monkeypatch):
    rows = [{"entityData": _entity(1), "documents": [{"id": "d1", "name": "a.md", "path": "/x"}],
             "document_count": 7, "_rowsTotal": 12, "_rawHits": 12}]
    out = _run_pattern(monkeypatch, ["pattern7", "--searchTerm", "alpha", "--shape", "brief"], rows)
    assert out["rows"][0]["documents"] == [{"id": "d1", "title": "a", "date": None}]
    assert out["rows"][0]["documents_total"] == 7
    assert out["rows_total"] == 12 and out["truncated"] is True
    assert out["next"].startswith("artmind query graph pattern7 --domain d --searchTerm alpha --shape brief --all --compact; ")
    assert "--shape full" in out["next"]


def test_pattern7_empty_brief_has_no_rows(monkeypatch):
    out = _run_pattern(monkeypatch, ["pattern7", "--searchTerm", "zz", "--shape", "brief"],
                       [{"entityData": None, "documents": [], "document_count": 0, "_rowsTotal": 0, "_rawHits": 0}])
    assert out["rows"] == [] and out["rows_total"] == 0


@pytest.mark.parametrize("pattern,extra", [("pattern1", ["--entityClass", "ORG"]),
                                           ("pattern2", ["--entityIdList", "e1"]),
                                           ("pattern7", ["--searchTerm", "a"]),
                                           ("pattern8", ["--entityClass", "ORG", "--entityName", "A"])])
def test_patterns_pass_shape_to_the_query_layer(pattern, extra):
    with patch("artmind.graph_query.execute_pattern", return_value={"rows": []}) as fn:
        _invoke(["query", "graph", pattern, "--domain", "d", *extra, "--shape", "brief", "--compact"])
        assert fn.call_args[1]["shape"] == "brief"
        _invoke(["query", "graph", pattern, "--domain", "d", *extra, "--compact"])
        assert fn.call_args[1]["shape"] == "full"


def test_other_patterns_refuse_brief():
    with pytest.raises(ValueError, match="not available"):
        gq.execute_pattern("d", "pattern3", shape="brief", entityIdList=["e1"])


# --- entity-context ----------------------------------------------------------------------


def _hub(n_summaries=78, n_connections=25, n_chunks=28, include=8):
    conns = []
    for i in range(n_summaries):  # 78 distinct (rel_type, direction) pairs
        conns.append({"rel_type": f"REL_{i // 2}", "direction": "out" if i % 2 == 0 else "in",
                      "target_id": f"t{i}", "target_name": f"Target number {i} of a hub", "target_class": "PERSON",
                      "observation_count": 3, "doc_count": 2, "score": None})
    refs = [{"id": f"d{i}_000", "name": f"Section {i} of the long minutes", "doc_id": f"d{i}",
             "current": True, "doc_valid_from": None, "score": None} for i in range(n_chunks)]
    big = " ".join(f"Sentence {i} of a deliberately long chunk." for i in range(60))  # > 1,200 chars
    return {
        "entity": [{"entityData": {**_entity(), "description": LONG}}],
        "connections": conns,
        "chunk_refs": refs,
        "chunk_keyword": [],
        "chunk_text": lambda p: [{"chunk": {"id": c, "doc_id": c.split("_")[0], "text": big,
                                            "document": {"id": c.split("_")[0], "name": f"{c.split('_')[0]}.md",
                                                         "valid_from": "2024-01-01"}}} for c in p["chunkIds"]],
        "source_documents": [{"document": {"id": f"d{i}", "name": f"d{i}.md", "path": "/long/path/x",
                                           "valid_from": "2024-01-01"}} for i in range(n_chunks)],
    }, n_connections, include


def test_entity_context_brief_hub_fits_the_agent_budget(monkeypatch):
    responses, max_conns, include = _hub()
    _install_session(monkeypatch, _RecordingSession(responses))
    full = gq.entity_context(["d"], "e1", include_chunks=include, max_connections=max_conns)
    _install_session(monkeypatch, _RecordingSession(_hub()[0]))
    out = gq.entity_context(["d"], "e1", include_chunks=include, max_connections=max_conns, shape="brief")
    row = out["rows"][0]
    assert len(row["connection_summary"]) == 78 and all(isinstance(s, str) for s in row["connection_summary"])
    assert row["connections_total"] == 78 and len(row["connections"]) == 25
    assert row["chunks_total"] == 28 and len(row["chunks"]) == 8
    assert row["more_chunks_total"] == 20 == len(row["more_chunks"])
    assert row["source_documents_total"] == 28 and len(row["source_documents"]) == 10
    assert set(row["connections"][0]) == {"rel_type", "direction", "id", "name", "class", "observations", "documents"}
    assert set(row["more_chunks"][0]) == {"id", "title", "doc_id"}
    assert set(row["source_documents"][0]) == {"id", "title", "date"}
    assert set(row["entity"]) == {"id", "name", "class", "description"}
    assert "--shape full" in out["next"]
    brief_chars = len(json.dumps(out, ensure_ascii=False))
    full_chars = len(json.dumps(full, ensure_ascii=False))
    print(f"hub entity-context: full={full_chars} brief={brief_chars}")
    assert brief_chars < 24000 and brief_chars < full_chars


def test_entity_context_full_shape_unchanged_keys(monkeypatch):
    _install_session(monkeypatch, _RecordingSession(_hub_responses()))
    row = gq.entity_context(["fiction"], "e1", include_chunks=2)["rows"][0]
    assert {"entityData", "connection_summary", "connections", "more_chunks", "source_documents"} <= set(row)
    assert isinstance(row["connection_summary"][0], dict)


# --- insights show -------------------------------------------------------------------------


def _finding() -> Finding:
    return Finding(
        key="outcome_lift:a", detector="outcome_lift", kind="corpus", score=0.8, title="A lifts B",
        subjects=[{"_id": f"s{i}", "name": f"S{i}", "class": "PERSON", "extra": "x" * 500} for i in range(14)],
        stats={"lift": 2.5, "n": 40, "days": [f"2024-01-{d:02d}" for d in range(1, 29)], "table": {"a": 1, "b": 2}},
        card={"rows": [{"x": 1}] * 30, "headline": "x"}, narration="A long narration. " * 30, diff="new")


def test_insights_show_brief_and_full(monkeypatch):
    run = SimpleNamespace(findings=[_finding()], as_of="2024-02-01", created_at="2024-02-02")
    monkeypatch.setattr("artmind.cli._latest_insights", lambda name: run)
    base = ["query", "insights", "show", "--domain", "d", "outcome_lift:a", "--compact"]
    full = _invoke(base)
    out = _invoke([*base, "--shape", "brief"])
    f = out["finding"]
    assert set(f) == {"key", "detector", "kind", "score", "title", "diff", "action", "subjects", "subjects_total",
                      "stats", "stats_counts", "card_counts"}
    assert f["subjects_total"] == 14 and len(f["subjects"]) == brief.FINDING_SUBJECTS
    assert set(f["subjects"][0]) == {"_id", "name", "class"}
    assert f["stats"] == {"lift": 2.5, "n": 40} and f["stats_counts"] == {"days": 28, "table": 2}
    assert f["card_counts"] == {"rows": 30, "headline": 1}
    assert "narration" not in f and "--shape full" in out["next"]
    assert len(json.dumps(out)) < len(json.dumps(full))
    assert full["finding"]["narration"] and len(full["finding"]["subjects"]) == 14  # full unchanged
