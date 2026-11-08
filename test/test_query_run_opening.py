"""Opened documents (P3 spec 6.5): selection, the open step in the executor, the bundle. Invented data, fake step_fn."""

import json

from artmind.query_run import bundle, executor, opening
from artmind.query_run.capture import StepResult


def _docs(*titles):
    return [{"doc_id": f"d{i}", "title": t} for i, t in enumerate(titles)]


def _ids(chosen):
    return [d for d, _ in chosen]


# ── selection ────────────────────────────────────────────────────────────
def test_top_five_by_rank():
    docs = _docs(*[f"Doc {i}" for i in range(8)])
    chosen = opening.select_documents(docs, [], "what is the plan")
    assert chosen == [(f"d{i}", "rank") for i in range(5)]


def test_documents_with_a_full_passage_are_skipped_and_the_next_one_takes_the_slot():
    docs = _docs(*[f"Doc {i}" for i in range(8)])
    evidence = [{"kind": "passage", "doc": {"id": "d0"}}, {"kind": "chunk", "doc": {"id": "d1"}}]
    chosen = opening.select_documents(docs, evidence, "x")
    assert _ids(chosen) == ["d1", "d2", "d3", "d4", "d5"]  # a chunk row is not a full passage


def test_title_match_by_year_version_and_word():
    docs = _docs("A", "B", "C", "D", "E", "Rate schedule 2026", "Policy_v3 final", "Mortgage_2026_03 notes", "Other")
    assert opening.select_documents(docs, [], "rates in 2026", title=2) == [
        *[(f"d{i}", "rank") for i in range(5)], ("d5", "title"), ("d7", "title")]
    assert opening.select_documents(docs, [], "what changed in v3?", title=2)[-1] == ("d6", "title")
    assert opening.select_documents(docs, [], "tell me about the mortgage", title=2)[-1] == ("d7", "title")


def test_title_terms_ignore_stopwords_and_short_words_and_fold_separators():
    assert opening.title_terms("What about these other things in 2025-03-01?") == ["things", "2025", "2025 03 01"]
    terms = opening.title_terms("which rate in 2026 for v3 and the cap?")
    assert "2026" in terms and "v3" in terms and "which" not in terms and "cap" not in terms
    docs = [{"doc_id": "x", "title": "rate-sheet_2026_03"}] + _docs(*"abcde")
    assert ("x", "title") in opening.select_documents(docs[1:] + docs[:1], [], "the 2026_03 sheet")


def test_title_cap_and_a_rank_pick_is_not_picked_twice():
    docs = _docs("Alpha 2026", "B", "C", "D", "E", "F 2026", "G 2026", "H 2026")
    chosen = opening.select_documents(docs, [], "2026")
    assert _ids(chosen)[:5] == ["d0", "d1", "d2", "d3", "d4"] and chosen[5:] == [("d5", "title"), ("d6", "title")]


def test_nothing_to_open():
    assert opening.select_documents([], [], "q") == []
    docs = _docs("A")
    assert opening.select_documents(docs, [{"kind": "passage", "doc": {"id": "d0"}}], "q") == []


def test_command_is_a_real_survey_expand():
    argv = opening.command(["d1", "d2"], ["bank", "hr"], "what rate?")
    assert argv == ["query", "survey", "--shape", "brief", "--domain", "bank,hr", "--expand", "d1", "--expand", "d2",
                    "--compact", "what rate?"]
    from artmind.query_run import capture
    assert capture.allowed(argv)


# ── executor ─────────────────────────────────────────────────────────────
def _doc(i):
    return {"id": f"d{i}", "title": f"Doc {i}", "domain": "bank", "date": None, "status": "current"}


LISTING = {"command": "query survey", "documents": [
    {"doc_id": f"d{i}", "title": f"Doc {i}", "domain": "bank", "status": "current", "gist": "g", "gist_source": "summary"}
    for i in range(7)]}


def _opened_payload(ids):
    return {"command": "query survey", "documents": [], "passages": [
        {"passage_id": f"op{i}", "chunk_id": f"oc{i}", "text": f"opened text {i}", "relevance": 0.5, "doc": _doc(int(i[1:]))}
        for i in ids]}


PLAN = {"program": [{"id": "s1", "kind": "run", "run": "artmind query survey --shape brief --domain bank q --compact", "filled": True}],
        "guards": [], "route": {"id": "survey"}, "replacements": {}, "question": "q", "effective_question": "what rate in 2026?",
        "scope": {"domains": ["bank"]}, "confidence": {"level": "low"}, "classifier": {"backend": "lexical"}}


def _runner(calls, open_result=None):
    def step(argv, sid):
        calls.append((sid, list(argv)))
        if sid == "open":
            if open_result is not None:
                return open_result(sid, argv)
            ids = [argv[i + 1] for i, a in enumerate(argv) if a == "--expand"]
            return StepResult(sid, list(argv), 0, None, _opened_payload(ids), 40.0, 500)
        return StepResult(sid, list(argv), 0, None, LISTING, 5.0, 100)
    return step


def test_the_open_step_runs_once_with_the_ids_question_and_domains():
    calls = []
    ex = executor.execute(PLAN, step_fn=_runner(calls))
    opens = [c for c in calls if c[0] == "open"]
    assert len(opens) == 1
    argv = opens[0][1]
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "--expand"] == ["d0", "d1", "d2", "d3", "d4"]
    assert argv[argv.index("--domain") + 1] == "bank" and argv[-1] == "what rate in 2026?" and "brief" in argv
    assert ex.executed == 2 and "open" in ex.results
    assert ex.path[-1]["id"] == "open" and ex.path[-1]["exit_code"] == 0 and ex.path[-1]["opened"] == 5


def test_the_open_step_counts_against_the_step_limit():
    calls = []
    ex = executor.execute(PLAN, step_fn=_runner(calls), max_steps=1)
    assert not any(c[0] == "open" for c in calls)
    assert ex.path[-1] == {"id": "open", "kind": "run", "skipped": "limit"} and ex.partial


def test_the_open_step_respects_the_deadline():
    now = [0.0]
    calls = []
    inner = _runner(calls)

    def slow(argv, sid):
        now[0] = 99.0  # the program's step used up the deadline
        return inner(argv, sid)

    ex = executor.execute(PLAN, step_fn=slow, deadline_s=5.0, clock=lambda: now[0])
    assert not any(c[0] == "open" for c in calls) and ex.path[-1]["skipped"] == "deadline"


def test_skips_are_recorded_with_a_reason():
    calls = []
    ex = executor.execute({**PLAN, "program": [], "answer_from": "previous"}, step_fn=_runner(calls))
    assert ex.escalate["reason"] == "no_lookup" and calls == []
    assert ex.path == [{"id": "open", "kind": "run", "skipped": "no_lookup"}]
    ex = executor.execute(PLAN, step_fn=_runner(calls), open_documents=False)
    assert not any(p["id"] == "open" for p in ex.path)
    ex = executor.execute({**PLAN, "scope": {"domains": []}}, step_fn=_runner([]))
    assert ex.path[-1]["skipped"] == "no_domains"
    empty = lambda argv, sid: StepResult(sid, list(argv), 0, None, {"command": "query survey", "documents": []}, 1.0, 1)
    ex = executor.execute(PLAN, step_fn=empty)
    assert ex.path[-1] == {"id": "open", "kind": "run", "skipped": "no_documents"}


def test_every_row_already_a_passage_skips():
    full = {"command": "query vector-text", "rows": [
        {"passage_id": f"p{i}", "chunk_id": f"c{i}", "text": "t", "relevance": 0.5, "doc": _doc(i)} for i in range(3)]}
    calls = []
    ex = executor.execute(PLAN, step_fn=lambda argv, sid: (calls.append(sid), StepResult(sid, list(argv), 0, None, full, 1.0, 1))[1])
    assert calls == ["s1"] and ex.path[-1]["skipped"] == "all_passaged"


# ── bundle ───────────────────────────────────────────────────────────────
def test_the_bundle_carries_opened_flags_and_opened_rows_first():
    calls = []
    ex = executor.execute(PLAN, step_fn=_runner(calls))
    printed, full = bundle.build(PLAN, ex, budget=24_000)
    assert [o["doc_id"] for o in printed["opened"]] == ["d0", "d1", "d2", "d3", "d4"]
    assert all(o["why"] == "rank" and len(o["passages"]) == 1 for o in printed["opened"])
    assert [e["from"] for e in printed["evidence"]] == [["open"]] * 5
    assert printed["evidence"][0]["eid"] == "e1" and printed["opened"][0]["passages"] == ["e1"]
    flags = {d["doc_id"]: d["opened"] for d in printed["documents"]}
    assert flags == {"d0": True, "d1": True, "d2": True, "d3": True, "d4": True, "d5": False, "d6": False}
    assert printed["opened"][0]["title"] == "Doc 0"


def test_opened_rows_are_never_cut_by_a_small_budget():
    def big(sid, argv):
        p = _opened_payload(["d0", "d1", "d2"])
        for r in p["passages"]:
            r["text"] = "x" * 1100
        return StepResult(sid, list(argv), 0, None, p, 1.0, 1)

    ex = executor.execute(PLAN, step_fn=_runner([], big))
    printed, full = bundle.build(PLAN, ex, budget=1_500)
    assert [e["from"] for e in printed["evidence"]] == [["open"]] * 3
    assert any("opened passages alone" in w for w in printed["warnings"])
    assert printed["truncated"] is True and printed["handle"]
    assert len(full["evidence"]) >= 3


def test_opened_rows_merge_with_the_same_ranked_passage():
    both = {"command": "query vector-text", "rows": [{"passage_id": "op0", "chunk_id": "oc0", "text": "t", "relevance": 0.5, "doc": _doc(9)}]}
    ex = executor.Execution()
    ex.results["s1"] = StepResult("s1", ["query", "vector-text"], 0, None, both, 1.0, 1)
    ex.results["open"] = StepResult("open", ["query", "survey"], 0, None, {"command": "query survey", "documents": [], "passages": [
        {"passage_id": "op0", "chunk_id": "oc0", "text": "t", "relevance": 0.5, "doc": _doc(9)}]}, 1.0, 1)
    ex.answer_ids = ["s1"]
    ex.opened = [{"doc_id": "d9", "why": "rank"}]
    printed, _ = bundle.build(PLAN, ex, budget=24_000)
    assert len(printed["evidence"]) == 1 and printed["evidence"][0]["from"] == ["open", "s1"]


def test_a_failed_open_step_leaves_the_bundle_and_warns():
    failing = lambda sid, argv: StepResult(sid, list(argv), 1, "boom", None, 1.0, 0)
    ex = executor.execute(PLAN, step_fn=_runner([], failing))
    printed, _ = bundle.build(PLAN, ex, budget=24_000)
    assert printed["opened"] == [] and printed["evidence"] == []
    assert all(d["opened"] is False for d in printed["documents"])
    assert any("opening documents failed" in w for w in printed["warnings"])
    assert ex.escalate is None or ex.escalate["reason"] != "step_error"
    json.dumps(printed)


def test_exit_code_ignores_a_failed_open_step():
    failing = lambda sid, argv: StepResult(sid, list(argv), 3, "store", None, 1.0, 0)
    ex = executor.execute(PLAN, step_fn=_runner([], failing))
    assert ex.exit_code == 0
