"""Regression questions out of dossiers; verifying a fix by re-running one."""

import json
import time

import pytest

from artmind import benchmark
from artmind.reviews import dossier as dz
from artmind.reviews import verify
from artmind.traces import store

REG = ("### R-rv-2031-01-01-aaaaaa — trips\n\n**Question:** How many trips did Mira take?\n\n"
       "**Evaluation comment:** Three, answered from the graph.")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")
    monkeypatch.setattr(paths, "REVIEWS_DIR", tmp_path / "reviews")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")


def _turn(tid, *, answer, fell_through, basis, commands=("query graph pattern6",)):
    store.insert_turn(tid, source="admin", question="How many trips did Mira take?", started_at=time.time())
    store.update_turn(tid, answer=answer, status="done", fell_through=int(fell_through), answer_basis=basis,
                      graph_attempts=1, graph_productive=0 if fell_through else 1, domains='["j"]',
                      retrieval_path="[]")
    for i, cmd in enumerate(commands):
        store.insert_span({"span_id": f"{tid}-{i}", "trace_id": tid, "command": cmd, "args": "{}", "exit_code": 0,
                           "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0, "evidence": "{}",
                           "started_at": time.time()}, [])


def _dossier(rid="rv-2031-01-01-aaaaaa", traces=("t-orig",), status="fixed", reg=REG, elements=("j/PERSON-WENT_TO->",)):
    sections = {t: "" for t in dz.SECTIONS}
    sections["Regression question"] = reg
    d = dz.Dossier(meta={"review_id": rid, "status": status, "stage": "extraction", "elements": list(elements),
                         "trace_ids": list(traces), "private_terms": []}, sections=sections)
    dz.save(d)
    return d


def test_regression_parses_the_one_question():
    r = verify.regression(_dossier())
    assert r == {"review_id": "rv-2031-01-01-aaaaaa", "title": "trips", "question": "How many trips did Mira take?",
                 "eval_comment": "Three, answered from the graph."}
    with pytest.raises(dz.DossierError):
        verify.regression(_dossier(rid="rv-2031-01-02-bbbbbb", reg="<!-- nothing yet -->"))


def test_regressions_markdown_round_trips_through_the_benchmark_parser():
    _dossier()
    _dossier(rid="rv-2031-01-02-bbbbbb", status="open",
             reg="### R-rv-2031-01-02-bbbbbb — x\n\n**Question:** Q2?\n\n**Evaluation comment:** E2.")
    text = verify.regressions_markdown()
    assert [q["question"] for q in benchmark._parse_questions(text)] == ["How many trips did Mira take?", "Q2?"]
    only_fixed = verify.regressions_markdown(statuses=("fixed",))
    assert [q["question"] for q in benchmark._parse_questions(only_fixed)] == ["How many trips did Mira take?"]


def _runner_recording(new_trace_id, seen):
    def run(run_id):
        seen["run_id"] = run_id
        seen["run"] = benchmark._get_benchmark_run(run_id)
        benchmark._update_benchmark_question(run_id, 1, status="completed", answer_text="Three.", trace_id=new_trace_id)
    return run


def test_verify_graph_miss_dossier_passes_when_the_graph_answers():
    _turn("t-orig", answer="Two.", fell_through=True, basis="vector", commands=("query graph pattern6", "query vector-text"))
    _turn("t-new", answer="Three.", fell_through=False, basis="graph")
    _dossier()
    seen = {}
    result = verify.verify("rv-2031-01-01-aaaaaa", runner=_runner_recording("t-new", seen))
    assert seen["run"]["domain_hint"] == "j" and seen["run"]["name"] == "verify rv-2031-01-01-aaaaaa"
    assert result["question"] == "How many trips did Mira take?"
    assert result["original"]["fell_through"] is True and result["new"]["answer_basis"] == "graph"
    assert [(c["check"], c["ok"]) for c in result["checks"]] == [("answered", True), ("graph_answer", True)]
    assert result["mechanical_ok"] is True
    log = dz.load("rv-2031-01-01-aaaaaa").sections["Log"]
    assert f"verify run `{seen['run_id']}` → trace `t-new`: mechanical checks pass" in log
    assert dz.load("rv-2031-01-01-aaaaaa").meta["status"] == "fixed"   # never auto-verified


def test_verify_graph_miss_fails_if_it_still_falls_through():
    _turn("t-orig", answer="Two.", fell_through=True, basis="vector")
    _turn("t-new", answer="Three.", fell_through=True, basis="vector")
    _dossier()
    result = verify.verify("rv-2031-01-01-aaaaaa", runner=_runner_recording("t-new", {}))
    assert result["mechanical_ok"] is False
    assert dict((c["check"], c["ok"]) for c in result["checks"])["graph_answer"] is False


def test_verify_non_graph_miss_only_checks_answered_and_accepts_an_existing_run():
    _turn("t-orig", answer="Two.", fell_through=False, basis="graph")
    _turn("t-new", answer="Three.", fell_through=False, basis="graph")
    _dossier()
    run_id = benchmark._create_benchmark_run("x", [{"seq": 1, "qid": "R", "title": "t", "question": "q",
                                                    "eval_comment": None}])
    benchmark._update_benchmark_question(run_id, 1, status="completed", trace_id="t-new")

    def must_not_run(_):
        raise AssertionError("an existing run must not be re-run")

    result = verify.verify("rv-2031-01-01-aaaaaa", run_id=run_id, runner=must_not_run)
    assert [c["check"] for c in result["checks"]] == ["answered"]
    assert result["run_id"] == run_id


def test_verify_untraced_run_fails_cleanly():
    _turn("t-orig", answer="Two.", fell_through=False, basis="graph")
    _dossier()

    def run(run_id):
        benchmark._update_benchmark_question(run_id, 1, status="failed", error_message="agent crashed")

    result = verify.verify("rv-2031-01-01-aaaaaa", runner=run)
    assert result["mechanical_ok"] is False and result["error"] == "agent crashed"
    assert result["checks"][0]["check"] == "traced"
