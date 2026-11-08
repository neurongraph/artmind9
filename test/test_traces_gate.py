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
    assert out["worse_than_all"] == [1] and out["lost_must"] == {1: ["x"]}


def test_above_band():
    b = {"balance": 1, "material": 1}
    vs = {"A": {1: V("material", "right"), 2: V("material", "right")},
          "D": {1: V("material", "right"), 2: V("minor", "right")}}
    assert gate.evaluate_variant(b, vs, baseline_found={}, variant_found={})["above_band"] is True
