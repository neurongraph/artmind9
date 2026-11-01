from artmind.retrieval_eval import metrics as m


def test_recall_at_k_is_hit_or_miss():
    assert m.recall_at_k(["a", "b", "c"], {"c"}, 3) == 1.0
    assert m.recall_at_k(["a", "b", "c"], {"c"}, 2) == 0.0


def test_reciprocal_rank_uses_first_hit():
    assert m.reciprocal_rank(["a", "b", "c"], {"b", "c"}) == 0.5
    assert m.reciprocal_rank(["a"], {"z"}) == 0.0


def test_docs_covered_is_a_fraction():
    assert m.docs_covered_at_k(["d1", "d1", "d3"], {"d1", "d2"}, 3) == 0.5
    assert m.docs_covered_at_k(["d1", "d2"], {"d1", "d2"}, 1) == 0.5


def test_summarise_by_kind():
    per = [
        {"kind": "needle", "latency_s": 1.0, "recall": {"5": 1.0, "20": 1.0}, "rr": 1.0},
        {"kind": "needle", "latency_s": 3.0, "recall": {"5": 0.0, "20": 1.0}, "rr": 0.25},
        {"kind": "survey", "latency_s": 2.0, "docs_covered": {"5": 0.5, "20": 1.0}},
    ]
    s = m.summarise(per, (5, 20))
    assert s["needle"] == {"questions": 2, "recall@5": 0.5, "recall@20": 1.0, "mrr": 0.625, "latency_s": 2.0}
    assert s["survey"] == {"questions": 1, "docs_covered@5": 0.5, "docs_covered@20": 1.0, "latency_s": 2.0}


def test_summarise_skips_absent_kinds():
    assert m.summarise([], (5,)) == {}
