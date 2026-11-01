# test/test_retrieval_runner.py
import json

from artmind.retrieval_eval import runner
from artmind.retrieval_eval.labels import RetrievalLabel

LABELS = [
    RetrievalLabel(id="r001", question="q1", domains=("p",), kind="needle", relevant=("c2",)),
    RetrievalLabel(id="r002", question="q2", domains=("p",), kind="survey", relevant_docs=("d1", "d9")),
]


def fake_search(calls):
    def search(domains, question, top_k):
        calls.append((tuple(domains), question, top_k))
        return {"rows": [
            {"chat": {"id": "x"}},  # chat rows are ignored
            {"chunk": {"id": "c1", "doc_id": "d1"}},
            {"chunk": {"id": "c2", "doc_id": "d2"}},
        ]}
    return search


def test_run_eval_scores_each_kind():
    calls = []
    ticks = iter([0.0, 0.5, 1.0, 2.0])
    run = runner.run_eval(LABELS, ks=(1, 5), search=fake_search(calls), clock=lambda: next(ticks))
    assert calls == [(("p",), "q1", 5), (("p",), "q2", 5)]
    q1, q2 = run["questions"]
    assert q1["chunk_ids"] == ["c1", "c2"] and q1["recall"] == {"1": 0.0, "5": 1.0} and q1["rr"] == 0.5
    assert q2["docs_covered"] == {"1": 0.5, "5": 0.5}
    assert run["summary"]["needle"]["mrr"] == 0.5
    assert run["ks"] == [1, 5] and "diversity" not in run and run["labels"] == 2


def test_default_search_calls_vector_text_with_top_k_only(monkeypatch):
    seen = []

    def vts(domains, question, top_k, **kwargs):
        seen.append((domains, question, top_k, kwargs))
        return {"rows": []}

    from artmind import vector_query
    monkeypatch.setattr(vector_query, "vector_text_search", vts)
    runner._default_search(["p"], "q", 5)
    assert seen == [(["p"], "q", 5, {})]


def test_write_and_compare(tmp_path):
    a = {"summary": {"needle": {"questions": 2, "recall@5": 0.5, "mrr": 0.4}}}
    b = {"summary": {"needle": {"questions": 2, "recall@5": 0.75, "mrr": 0.5}}}
    path = runner.write_run(a, tmp_path)
    assert json.loads(path.read_text())["summary"] == a["summary"]
    assert runner.compare_runs(a, b) == {"needle": {"recall@5": 0.25, "mrr": 0.1}}
