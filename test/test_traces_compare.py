"""Comparing benchmark runs (P3 spec §9.2): per-question pairing and mechanical measures. Invented data."""

import json

import pytest

from artmind.traces import compare, store


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _run(run_id, contract, answers, *, durations=None, costs=None, ratings=None, spans=None):
    """A completed benchmark run with one trace turn per answer."""
    from artmind import benchmark

    qs = [{"seq": i, "qid": f"Q{i:02d}", "title": f"t{i}", "question": f"invented question {i}?",
           "eval_comment": f"expect fact {i}"} for i in range(1, len(answers) + 1)]
    rid = benchmark._create_benchmark_run("/tmp/q.md", qs, contract=contract)
    conn = store._connect()
    for i, answer in enumerate(answers, 1):
        tid = f"{run_id}-{i}"
        store.insert_turn(tid, source="benchmark", question=qs[i - 1]["question"], benchmark_run_id=rid,
                          benchmark_seq=i, contract=contract, started_at=1_700_000_000.0 + i)
        store.update_turn(tid, status="done", answer=answer, duration_s=(durations or {}).get(i, 20.0),
                          cost_usd=(costs or {}).get(i, 0.1), turns=3)
        conn.execute("UPDATE benchmark_questions SET answer_text=?, trace_id=?, status='done', duration_s=?, cost_usd=?"
                     " WHERE run_id=? AND seq=?",
                     (answer, tid, (durations or {}).get(i, 20.0), (costs or {}).get(i, 0.1), rid, i))
        conn.commit()  # release the write lock before the store opens its own connections
        for n, docs in enumerate((spans or {}).get(i, [["d1"]])):
            store.insert_span({"span_id": f"{tid}-s{n}", "trace_id": tid, "command": "query vector-text", "args": "{}",
                               "exit_code": 0, "duration_ms": 1, "rows": 1, "rows_total": None, "truncated": 0,
                               "evidence": json.dumps({"documents": [{"doc_id": d} for d in docs]}),
                               "started_at": 1_700_000_000.5 + i}, [])
        if ratings and i in ratings:
            store.add_feedback(tid, ratings[i])
    conn.commit()
    conn.close()
    return rid


def test_runs_pair_by_seq_and_carry_their_contract():
    a = _run("a", "classic", ["ans a1", "ans a2"])
    b = _run("b", "bundle", ["ans b1", "ans b2"])
    runs = compare.load_runs([a, b])
    assert [r.contract for r in runs] == ["classic", "bundle"]
    pairs = compare.pair(runs[0], runs[1])
    assert [(p.seq, p.qid) for p in pairs] == [(1, "Q01"), (2, "Q02")]
    assert pairs[0].question == "invented question 1?" and pairs[0].expected == "expect fact 1"
    assert (pairs[0].left.answer, pairs[0].right.answer) == ("ans a1", "ans b1")


def test_runs_of_different_question_files_do_not_pair():
    a = _run("a", "classic", ["x"])
    from artmind import benchmark

    other = benchmark._create_benchmark_run("/tmp/other.md", [{"seq": 1, "qid": "Z1", "title": "t",
                                                               "question": "different?", "eval_comment": None}])
    with pytest.raises(compare.CompareError, match="same questions"):
        compare.pair(*compare.load_runs([a, other]))


def test_mechanical_measures():
    a = _run("a", "classic", ["x", "y", "z"], durations={1: 10, 2: 20, 3: 30}, costs={1: 0.1, 2: 0.2, 3: 0.3},
             ratings={1: "good", 2: "good", 3: "wrong"}, spans={1: [["d1"], ["d2"]], 2: [["d1"]], 3: [["d3"]]})
    m = compare.measures(compare.load_runs([a])[0])
    assert m["questions"] == 3 and m["answered"] == 3
    assert m["ratings"] == {"good": 2, "partial": 0, "wrong": 1, "unrated": 0}
    assert m["duration_s_median"] == 20 and m["cost_usd_median"] == 0.2
    assert m["commands_median"] == 1  # top-level spans per turn: 2, 1, 1


def test_a_missing_run_is_an_error():
    with pytest.raises(compare.CompareError, match="no benchmark run"):
        compare.load_runs(["nope"])


def test_a_run_id_prefix_resolves():
    a = _run("a", "classic", ["x"])
    assert compare.load_runs([a[:8]])[0].run_id == a


def test_answer_reach_uses_labels_linked_to_benchmark_questions():
    from artmind.query_plan.labels import Label

    a = _run("a", "classic", ["x", "y"], spans={1: [["policies/fees.md"]], 2: [["other.md"]]})
    run = compare.load_runs([a])[0]
    labels = [Label(id="L1", question="Invented  Question 1?", source="benchmark", domains=("b",), route="search",
                    view=None, params={}, expected_documents=("policies/fees.md", "rates.md"))]
    reach = compare.answer_reach(run, labels)
    assert reach == {"labels": 1, "expected": 2, "reached": 1, "by_qid": {"Q01": [1, 2]}}


def test_answer_reach_without_labels_is_none():
    a = _run("a", "classic", ["x"])
    assert compare.answer_reach(compare.load_runs([a])[0], []) is None
