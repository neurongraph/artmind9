from artmind.insights.diff import apply_diff
from artmind.insights.model import Finding


def f(key, score=0.5, headline=10, members=None, detector="dormancy", diff="new"):
    stats = {"headline": headline}
    if members is not None:
        stats["members"] = members
    return Finding(key=key, detector=detector, kind="corpus", score=score, title=key, stats=stats, diff=diff)


def test_first_run_marks_everything_new():
    out, summary = apply_diff([f("a"), f("b")], None, None)
    assert [x.diff for x in out] == ["new", "new"]
    assert summary == {"previous": None, "new": 2, "changed": 0, "same": 0, "gone": 0}


def test_new_changed_same_gone():
    prev = [f("same"), f("score", score=0.2), f("head", headline=10), f("old"), f("was_gone", diff="gone")]
    cur = [f("same"), f("score", score=0.5), f("head", headline=20), f("fresh")]

    out, summary = apply_diff(cur, prev, "r1.json")

    states = {x.key: x.diff for x in out}
    assert states == {"same": "same", "score": "changed", "head": "changed", "fresh": "new", "old": "gone"}
    assert summary == {"previous": "r1.json", "new": 1, "changed": 2, "same": 1, "gone": 1}


def test_circle_keeps_its_key_when_its_lead_changes():
    prev = [f("communities:PERSON:circle:a1", members=["a1", "a2", "a3", "a4"], detector="communities")]
    cur = [f("communities:PERSON:circle:a2", members=["a2", "a1", "a3", "a5"], detector="communities")]

    out, summary = apply_diff(cur, prev, "r1.json")

    assert [x.key for x in out] == ["communities:PERSON:circle:a1"]
    assert summary["gone"] == 0 and summary["new"] == 0


def test_dissimilar_circle_is_new():
    prev = [f("communities:PERSON:circle:a1", members=["a1", "a2", "a3"], detector="communities")]
    cur = [f("communities:PERSON:circle:b1", members=["b1", "b2", "b3"], detector="communities")]
    out, summary = apply_diff(cur, prev, "r1.json")
    assert summary["new"] == 1 and summary["gone"] == 1
