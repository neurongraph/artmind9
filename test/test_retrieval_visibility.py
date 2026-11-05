"""evidence_visible and the brief-vs-full visibility gate (bounded-output spec 4.3). Synthetic payloads."""
import json

from artmind.retrieval_eval import metrics, runner
from artmind.retrieval_eval.labels import RetrievalLabel


def full_survey():
    return {"documents": [
        {"doc_id": "d1", "passages": [{"id": "p1", "chunk_id": "c1", "text": "alpha"}]},
        {"doc_id": "d2", "passages": []},
    ]}


def brief_survey():
    return {"documents": [
        {"doc_id": "d1", "passage": {"passage_id": "p1", "chunk_id": "c1", "text": "alpha"},
         "gist_source": "passage", "best": {"chunk_id": "c1"}},
        {"doc_id": "d2", "gist": "g", "gist_source": "passage", "best": {"chunk_id": "c2"}},
        {"doc_id": "d3", "gist": "g", "gist_source": "chunk_summary", "best": {"chunk_id": "c3"}},
        {"doc_id": "d4", "title": "line only"},
    ]}


def test_needle_full_and_brief_survey():
    assert metrics.evidence_visible(full_survey(), "needle", {"c1"}) == 1.0
    assert metrics.evidence_visible(full_survey(), "needle", {"c2"}) == 0.0
    assert metrics.evidence_visible(brief_survey(), "needle", {"c2"}) == 1.0     # passage gist
    assert metrics.evidence_visible(brief_survey(), "needle", {"c3"}) == 0.0     # summary gist is not evidence


def test_survey_share():
    assert metrics.evidence_visible(full_survey(), "survey", {"d1", "d2"}) == 0.5
    assert metrics.evidence_visible(brief_survey(), "survey", {"d1", "d2", "d3", "d4"}) == 0.5
    assert metrics.evidence_visible(brief_survey(), "survey", set()) == 0.0


def test_vector_text_full_and_brief():
    full = {"rows": [{"chat": {"id": "x"}}, {"chunk": {"id": "c7", "doc_id": "d7"}}]}
    brief = {"rows": [{"rank": 1, "passage_id": "p", "chunk_id": "c8", "text": "t", "doc": {"id": "d8"}}]}
    assert metrics.evidence_visible(full, "needle", {"c7"}) == 1.0
    assert metrics.evidence_visible(brief, "needle", {"c8"}) == 1.0
    assert metrics.evidence_visible(brief, "needle", {"c7"}) == 0.0
    assert metrics.evidence_visible(brief, "survey", {"d8", "d9"}) == 0.5


def test_run_eval_reports_evidence_visible_and_budget_cut(monkeypatch):
    labels = [RetrievalLabel(id="r1", question="q", domains=("p",), kind="needle", relevant=("c1",))]
    big = {"rows": [{"rank": i, "passage_id": f"p{i}", "chunk_id": f"c{i}", "text": "x" * 500, "doc": {"id": f"d{i}"}}
                    for i in range(1, 30)]}
    run = runner.run_eval(labels, search=lambda d, q, k: big, measure_visible=True)
    assert run["summary"]["needle"]["evidence_visible"] == 1.0
    monkeypatch.setenv("ARTMIND_OUTPUT_BUDGET", "2000")
    shown = runner.budget_view(big, "vector-text")
    assert len(json.dumps(shown, separators=(",", ":"))) < len(json.dumps(big, separators=(",", ":")))


def test_visibility_gate():
    def run(n, s):
        return {"summary": {"needle": {"evidence_visible": n}, "survey": {"evidence_visible": s}}}
    assert runner.visibility_gate(run(0.5, 0.4), run(0.5, 0.6)) == {"needle_ok": True, "survey_ok": True}
    assert runner.visibility_gate(run(0.6, 0.4), run(0.5, 0.6)) == {"needle_ok": False, "survey_ok": True}
    assert runner.visibility_gate(run(0.5, 0.7), run(0.5, 0.6))["survey_ok"] is False
