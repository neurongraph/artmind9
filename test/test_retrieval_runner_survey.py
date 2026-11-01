# test/test_retrieval_runner_survey.py
from artmind.retrieval_eval import runner
from artmind.retrieval_eval.labels import RetrievalLabel

SURVEY = RetrievalLabel(id="s1", question="q", domains=("notes",), kind="survey", relevant_docs=("A", "B"))
NEEDLE = RetrievalLabel(id="n1", question="q", domains=("notes",), kind="needle", relevant=("c1",))


def test_a_survey_payload_is_scored_by_its_documents():
    def search(domains, question, top):
        return {"documents": [{"doc_id": "B"}, {"doc_id": "X"}, {"doc_id": "A"}]}

    run = runner.run_eval([SURVEY], ks=(2, 10), search=search, clock=iter([0.0, 1.0]).__next__)
    q = run["questions"][0]
    assert q["docs_covered"] == {"2": 0.5, "10": 1.0}
    assert run["summary"]["survey"]["docs_covered@10"] == 1.0


def test_only_kinds_restricts_the_labels_run():
    calls = []

    def search(domains, question, top):
        calls.append(question)
        return {"documents": []}

    run = runner.run_eval([SURVEY, NEEDLE], search=search, only_kinds=("survey",))
    assert run["labels"] == 1 and len(calls) == 1


def test_survey_search_wraps_legs(monkeypatch):
    seen = {}

    def fake(domains, question, top_docs=10, passages=1, *, legs=("D", "C", "P"), topics=None):
        seen.update(domains=domains, top_docs=top_docs, legs=legs)
        return {"documents": []}

    import artmind.survey as survey
    monkeypatch.setattr(survey, "survey_search", fake)
    runner.survey_search_fn(("P", "D"))(["notes"], "q", 20)
    assert seen == {"domains": ["notes"], "top_docs": 20, "legs": ("P", "D")}
