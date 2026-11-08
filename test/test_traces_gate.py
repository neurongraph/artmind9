import json

from artmind.traces import gate


def V(verdict, better):
    return {"verdict": verdict, "better": better}


def test_balance():
    assert gate.balance({1: V("material", "left"), 2: V("minor", "right"), 3: V("same", "equal"),
                         4: V("unjudged", None)}) == 0


def test_band_is_the_widest_pair():
    pairs = {("A", "D"): {1: V("material", "left"), 2: V("material", "left")},
             ("A", "E"): {1: V("minor", "right")}}
    assert gate.band(pairs) == {"balance": 2, "material": 2}


def test_variant_inside_the_band_passes():
    b = {"balance": 2, "material": 3}
    vs = {"A": {1: V("material", "left"), 2: V("same", "equal")},
          "D": {1: V("minor", "right"), 2: V("same", "equal")}}
    out = gate.evaluate_variant(b, vs, baseline_found={"A": {1: {"x"}}, "D": {1: {"x"}}}, variant_found={1: {"x"}})
    assert out["passed"] and not out["above_band"]


def test_lost_must_and_worse_than_all_fail():
    b = {"balance": 5, "material": 5}
    vs = {"A": {1: V("material", "left")}, "D": {1: V("material", "left")}}
    out = gate.evaluate_variant(b, vs, baseline_found={"A": {1: {"x", "y"}}, "D": {1: {"x"}}},
                                variant_found={1: set()})
    assert not out["passed"]
    assert out["worse_than_all"] == [1] and out["lost_must_items"] == {1: ["x"]}
    assert "x" not in json.dumps(out["rows"])


def test_above_band():
    b = {"balance": 1, "material": 1}
    vs = {"A": {1: V("material", "right"), 2: V("material", "right")},
          "D": {1: V("material", "right"), 2: V("minor", "right")}}
    assert gate.evaluate_variant(b, vs, baseline_found={}, variant_found={})["above_band"] is True


def test_a_baseline_run_that_never_asked_does_not_empty_the_intersection():
    b = {"balance": 5, "material": 5}
    vs = {"A": {}, "D": {}}
    out = gate.evaluate_variant(b, vs, baseline_found={"A": {}, "D": {"q": {"x"}}}, variant_found={"q": set()})
    assert out["lost_must_items"] == {"q": ["x"]}


def test_variant_missing_is_not_lost_must():
    out = gate.evaluate_variant({"balance": 0, "material": 0}, {"A": {}},
                                baseline_found={"A": {"q": {"x"}}}, variant_found={})
    assert out["variant_missing"] == ["q"] and out["lost_must_items"] == {} and out["passed"]


def test_not_graded_skips_the_lost_must_row():
    out = gate.evaluate_variant({"balance": 0, "material": 0}, {"A": {}}, baseline_found={}, variant_found=None)
    row = next(r for r in out["rows"] if r["row"] == "no_lost_must")
    assert row["ok"] is None and out["graded"] is False and out["passed"]
