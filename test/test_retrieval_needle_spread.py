# test/test_retrieval_needle_spread.py -- per-question needle stats and NEEDLE_SPREAD calibration (invented data)
from artmind.retrieval_eval import metrics as m
from artmind.retrieval_eval import runner
from artmind.retrieval_eval.labels import RetrievalLabel


def _row(doc, rel):
    return {"chunk": {"id": f"c-{doc}-{rel}", "doc_id": doc}, "relevance": rel}


def test_needle_stats_counts_documents_and_drop():
    rows = [_row("d1", 0.9), _row("d1", 0.8), _row("d2", 0.7)] + [_row(f"x{i}", 0.5) for i in range(10)]
    # only the first ten rows count: d1, d2 and seven x's
    assert m.needle_stats(rows) == {"distinct_documents": 9, "score_drop": 0.1}


def test_needle_stats_none_drop_without_relevance():
    rows = [{"chunk": {"id": "a", "doc_id": "d1"}, "relevance": None}, _row("d2", 0.7)]
    assert m.needle_stats(rows) == {"distinct_documents": 2, "score_drop": None}
    assert m.needle_stats([_row("d1", 0.9)])["score_drop"] is None
    assert m.needle_stats([]) == {"distinct_documents": 0, "score_drop": None}


def test_needle_stats_brief_and_chat_rows():
    rows = [{"chat": {"id": "z"}, "relevance": 0.9}, {"chunk_id": "c", "doc": {"id": "d1"}, "relevance": 0.5}]
    assert m.needle_stats(rows)["distinct_documents"] == 1


def test_run_eval_records_stats_per_question():
    labels = [
        RetrievalLabel(id="r1", question="q", domains=("p",), kind="needle", relevant=("c-d1-0.9",)),
        RetrievalLabel(id="r2", question="q", domains=("p",), kind="survey", relevant_docs=("d1",)),
    ]
    run = runner.run_eval(labels, ks=(5,), search=lambda d, q, k: {"rows": [_row("d1", 0.9), _row("d2", 0.8)]})
    q1, q2 = run["questions"]
    assert q1["distinct_documents"] == 2 and q1["score_drop"] == 0.1 and q1["in_top10"] is True
    assert q2["distinct_documents"] == 2 and "in_top10" not in q2


def _q(id, kind, docs, drop, hit=True):
    q = {"id": id, "kind": kind, "distinct_documents": docs, "score_drop": drop}
    if kind == "needle":
        q["in_top10"] = hit
    return q


def test_needle_spread_table_and_recommendation():
    qs = [
        _q("n1", "needle", 2, 0.10), _q("n2", "needle", 4, 0.05), _q("n3", "needle", 6, 0.20, hit=False),
        _q("n4", "needle", 3, None),
        _q("s1", "survey", 5, 0.10), _q("s2", "survey", 9, 0.5),
    ]
    t = m.needle_spread_table(qs, 0.03)
    by = {c["s"]: c for c in t["candidates"]}
    assert by[2]["needle_answered_alone"] == 0 and by[2]["needle_precision"] is None and not by[2]["meets"]
    assert by[3]["needle_answered_alone"] == 1 and by[3]["needle_precision"] == 1.0 and by[3]["meets"]
    assert by[5]["needle_answered_alone"] == 2 and by[5]["meets"]
    assert by[6]["survey_answered_alone"] == 1 and by[6]["survey_ids"] == ["s1"] and not by[6]["meets"]
    assert by[7]["needle_misses"] == ["n3"] and not by[7]["meets"]
    assert t["recommended"] == 5
    assert t["labels"] == {"needle": 4, "survey": 2}


def test_needle_spread_none_meets_recommends_two():
    t = m.needle_spread_table([_q("s1", "survey", 1, 0.5)], 0.03)
    assert t["recommended"] == 2 and not any(c["meets"] for c in t["candidates"])
