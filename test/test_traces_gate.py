"""The P3b gate from measures and judgements (P3 spec §9.2, variance band)."""

from artmind.traces import gate


def _j(*pairs):
    """[(verdict, better)] per seq."""
    return {i: {"verdict": v, "better": b} for i, (v, b) in enumerate(pairs, 1)}


M = lambda contract, good, wrong, cmds, dur, cost: {  # noqa: E731
    "contract": contract, "questions": 10, "answered": 10,
    "ratings": {"good": good, "partial": 10 - good - wrong, "wrong": wrong, "unrated": 0},
    "commands_median": cmds, "duration_s_median": dur, "cost_usd_median": cost}


def test_balance_counts_left_minus_right():
    assert gate.balance(_j(("material", "left"), ("minor", "right"), ("same", "equal"), ("material", "left"))) == 1


def test_a_passing_set():
    classic_a, classic_b, bundle = M("classic", 7, 1, 6, 30, 0.3), M("classic", 6, 1, 6, 31, 0.3), M("bundle", 7, 1, 3, 20, 0.2)
    # A vs B: A better on 2, B better on 1 -> band 1. A vs bundle: A better on 1, bundle on 1 -> balance 0.
    ab = _j(("material", "left"), ("material", "left"), ("minor", "right"))
    a_bundle = _j(("material", "left"), ("minor", "right"), ("same", "equal"))
    b_bundle = _j(("minor", "equal"), ("material", "right"), ("same", "equal"))
    g = gate.evaluate(classic_a, classic_b, bundle, ab, a_bundle, b_bundle)
    assert g["passed"] is True and g["band"] == 1 and g["balance"] == 0
    assert g["worse_than_both"] == []


def test_bundle_materially_worse_than_both_classic_runs_fails():
    classic_a, classic_b, bundle = M("classic", 7, 1, 6, 30, 0.3), M("classic", 7, 1, 6, 30, 0.3), M("bundle", 7, 1, 3, 20, 0.2)
    ab = _j(("same", "equal"), ("same", "equal"))
    a_bundle = _j(("material", "left"), ("same", "equal"))
    b_bundle = _j(("material", "left"), ("same", "equal"))
    g = gate.evaluate(classic_a, classic_b, bundle, ab, a_bundle, b_bundle)
    assert g["passed"] is False and g["worse_than_both"] == [1]
    assert "materially worse than both classic runs" in " ".join(g["reasons"])


def test_ratings_and_cost_rows():
    classic_a, classic_b = M("classic", 8, 0, 6, 30, 0.3), M("classic", 8, 0, 6, 30, 0.3)
    bundle = M("bundle", 6, 2, 7, 40, 0.5)  # lost 2 good, more commands, slower, dearer
    same = _j(("same", "equal"))
    g = gate.evaluate(classic_a, classic_b, bundle, same, same, same)
    assert g["passed"] is False
    assert {r["row"] for r in g["rows"] if not r["ok"]} == {"rated_good", "commands", "latency", "cost"}


def test_unrated_runs_skip_the_rating_row():
    m = lambda c: {**M(c, 0, 0, 5, 30, 0.3), "ratings": {"good": 0, "partial": 0, "wrong": 0, "unrated": 10}}  # noqa: E731
    same = _j(("same", "equal"))
    g = gate.evaluate(m("classic"), m("classic"), {**m("bundle"), "commands_median": 3, "duration_s_median": 20}, same, same, same)
    assert next(r for r in g["rows"] if r["row"] == "rated_good")["ok"] is None  # not measured


def test_answer_reach_row_only_when_both_measured():
    base = M("classic", 7, 1, 6, 30, 0.3)
    same = _j(("same", "equal"))
    reach = lambda n: {"labels": 1, "expected": 3, "reached": n, "by_qid": {}}  # noqa: E731
    g = gate.evaluate({**base, "answer_reach": reach(3)}, base, {**M("bundle", 7, 1, 3, 20, 0.2), "answer_reach": reach(2)},
                      same, same, same)
    assert next(r for r in g["rows"] if r["row"] == "answer_reach")["ok"] is False and g["passed"] is False
    g = gate.evaluate(base, base, M("bundle", 7, 1, 3, 20, 0.2), same, same, same)
    assert all(r["row"] != "answer_reach" for r in g["rows"])
