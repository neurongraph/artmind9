import json

import pytest

from artmind.traces import grade
from artmind.traces.tiers import Tiers

T = Tiers(must=("fact one", "fact two"), optional=("fact three",), must_not=("wrong claim",))


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "reg.db")


def _llm(payload):
    calls = []

    def llm(prompt):
        calls.append(prompt)
        return json.dumps(payload)
    return llm, calls


def test_grade_reads_items_by_position():
    llm, _ = _llm({"must": [True, False], "must_not": [False], "grade": "partial", "reason": "r"})
    g = grade.grade_answer("q?", T, "an answer", llm=llm)
    assert g.grade == "partial" and g.must_found == ("fact one",) and g.must_missed == ("fact two",)
    assert g.must_not_hit == ()


def test_a_must_not_hit_is_never_correct():
    llm, _ = _llm({"must": [True, True], "must_not": [True], "grade": "correct", "reason": "r"})
    assert grade.grade_answer("q?", T, "a", llm=llm).grade == "partial"


def test_untiered_empty_and_over_ceiling():
    llm, calls = _llm({})
    assert grade.grade_answer("q?", Tiers(notes="x"), "a", llm=llm).grade == "ungraded"
    empty = grade.grade_answer("q?", T, "  ", llm=llm)
    assert empty.grade == "wrong" and empty.must_missed == T.must
    assert grade.grade_answer("q?", T, "y" * (grade.ANSWER_CEILING + 1), llm=llm).grade == "ungraded"
    assert calls == []


def test_bad_judge_output_is_ungraded():
    llm, _ = _llm({"must": [True], "grade": "excellent"})
    assert grade.grade_answer("q?", T, "a", llm=llm).grade == "ungraded"


def test_cached_grade_calls_once():
    llm, calls = _llm({"must": [True, True], "must_not": [False], "grade": "correct", "reason": "r"})
    g1 = grade.cached_grade("run1", 1, "q?", T, "a", llm=llm, model="m")
    g2 = grade.cached_grade("run1", 1, "q?", T, "a", llm=llm, model="m")
    assert g1 == g2 and g1.grade == "correct" and len(calls) == 1


def test_prose_before_the_object_is_still_graded():
    from artmind.traces import grade as g
    from artmind.traces.tiers import Tiers

    tiers = Tiers(must=("A", "B"), optional=(), must_not=(), notes="")
    raw = ('Checking the must items: [1] found, [2] missing.\n'
           '{"must": [true, false], "must_not": [], "grade": "partial", "reason": "B missing."}')
    got = g.grade_answer("q?", tiers, "some answer", llm=lambda p: raw)
    assert got.grade == "partial" and got.must_found == ("A",) and got.must_missed == ("B",)


def test_a_missing_grade_key_is_derived_from_the_item_verdicts():
    from artmind.traces import grade as g
    from artmind.traces.tiers import Tiers

    tiers = Tiers(must=("A", "B"), optional=(), must_not=("X",), notes="")
    raw = '{"must": [true, false], "must_not": [false], "reason": "B missing."}'
    got = g.grade_answer("q?", tiers, "some answer", llm=lambda p: raw)
    assert got.grade == "partial" and got.must_missed == ("B",)
    raw_all = '{"must": [true, true], "must_not": [true], "reason": "X stated."}'
    assert g.grade_answer("q?", tiers, "a", llm=lambda p: raw_all).grade == "partial"
