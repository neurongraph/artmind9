# test/test_retrieval_generate.py
from _neo4j_recorder import Recorder

from artmind.retrieval_eval import generate

CHUNKS = [{"id": f"d1_{i:03d}", "doc_id": "d1", "text": "x" * 300} for i in range(1, 6)]
CHUNKS.append({"id": "d1_999", "doc_id": "d1", "text": "short"})


def session():
    return Recorder(lambda cypher, params: CHUNKS if "MATCH (c:DocChunk)" in cypher else [])


def test_sample_is_seeded_and_skips_short_chunks():
    a = generate.sample_chunks(session(), "papers", 3, seed=7)
    b = generate.sample_chunks(session(), "papers", 3, seed=7)
    assert [r["id"] for r in a] == [r["id"] for r in b]
    assert len(a) == 3 and "d1_999" not in [r["id"] for r in a]


def test_sample_scopes_the_domain_family():
    s = session()
    generate.sample_chunks(s, "papers", 2, seed=1)
    cypher, params = s.calls[0]
    assert "c._domain = $domain OR c._domain STARTS WITH ($domain + '.')" in cypher
    assert params["domain"] == "papers"


def test_generate_builds_needle_labels_and_reports_failures():
    answers = iter(['{"question": "What does it say?"}', "not json", '{"question": ""}'])
    labels, warnings = generate.generate_labels(session(), "papers", 3, seed=2, llm=lambda p: next(answers))
    assert len(labels) == 1
    label = labels[0]
    assert label.kind == "needle" and label.source == "synthetic" and label.domains == ("papers",)
    assert label.question == "What does it say?" and len(label.relevant) == 1
    assert len(warnings) == 2


def test_prompt_asks_for_paraphrase_and_carries_the_text():
    seen = []
    generate.generate_labels(session(), "papers", 1, seed=3, llm=lambda p: seen.append(p) or '{"question": "q?"}')
    assert "Paraphrase" in seen[0] and "x" * 300 in seen[0]
