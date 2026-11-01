from artmind import retrieval_rank as rr


def _row(i, doc, cos=None):
    return {"id": i, "doc_id": doc, **({"cosine": cos} if cos is not None else {})}


def test_fuse_ids_weights_the_vector_leg_two_to_one_and_keeps_the_best_cosine():
    out = rr.fuse_ids([_row("a", "A", 0.9), _row("b", "B", 0.8)], [_row("b", "B")])
    assert [e["row"]["id"] for e in out] == ["b", "a"]          # 2/62 + 1/61 > 2/61
    assert out[0]["matched_by"] == ["vector", "fulltext"] and out[0]["cosine"] == 0.8
    assert out[1]["matched_by"] == ["vector"]


def test_fuse_ids_breaks_ties_by_id():
    out = rr.fuse_ids([], [_row("z", "Z"), _row("y", "Y")])
    assert [e["row"]["id"] for e in out] == ["z", "y"]          # rank 1 beats rank 2
    tie = rr.fuse_ids([_row("b", "B")], [_row("a", "A")])
    assert [e["row"]["id"] for e in tie] == ["b", "a"]          # 2/61 > 1/61


def test_first_per_doc_keeps_each_documents_best_rank():
    items = [{"row": _row("x", "B")}, {"row": _row("y", "A")}, {"row": _row("z", "B")}, {"row": {"id": "n"}}]
    assert rr.first_per_doc(items) == ["B", "A"]


def test_survey_fuse_sums_leg_votes_and_reports_ranks():
    out = rr.survey_fuse({"P": ["B", "A"], "D": ["C"], "C": ["B"]})
    assert [d for d, _, _ in out] == ["B", "C", "A"]
    assert out[0][2] == {"P": 1, "C": 1}
    assert out[1][2] == {"D": 1}


def test_survey_fuse_honours_weights_and_is_stable():
    out = rr.survey_fuse({"P": ["A"], "D": ["B"]}, weights={"P": 1.0, "D": 3.0})
    assert [d for d, _, _ in out] == ["B", "A"]
    even = rr.survey_fuse({"P": ["A"], "D": ["B"]})
    assert [d for d, _, _ in even] == ["A", "B"]                # equal score: by id
    assert rr.survey_fuse({}) == []
