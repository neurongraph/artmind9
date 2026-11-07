"""The executor fills step bindings at run time (P3 spec §4). Fake steps, invented payloads."""

from artmind.query_run import executor
from artmind.query_run.capture import StepResult

VT = "artmind query vector-text --shape brief --domain journal --topK 5 trip --compact"
CHUNKS = "artmind query chunks --idList {anchor.top_chunk} --expand 2 --shape brief --compact"
P10 = "artmind query graph pattern10 --domain journal --documentName {doc} --compact"


def _rows(*docs):
    return {"command": "query vector-text", "rows": [
        {"passage_id": f"p{i}", "chunk_id": f"c{i}", "text": "t", "doc": {"id": d, "title": t, "date": dt}}
        for i, (d, t, dt) in enumerate(docs)]}


class Steps:
    def __init__(self, by_word):
        self.by_word, self.calls = by_word, []

    def __call__(self, argv, sid):
        self.calls.append((sid, list(argv)))
        return StepResult(sid, list(argv), 0, None, self.by_word[argv[1] if argv[1] != "graph" else argv[2]], 1.0, 10)


def _plan(program):
    return {"program": program, "guards": [], "route": {"id": "episode"}, "replacements": {}}


ANCHOR = _rows(("d1", "Day 1 & 2; rm", "2026-03-01"), ("d2", "Day 2", "2026-03-02"), ("d9", "Day 9", "2026-03-09"))


def test_a_scalar_binding_becomes_one_argument():
    steps = Steps({"vector-text": ANCHOR, "chunks": {"command": "query chunks", "rows": []}})
    executor.execute(_plan([{"id": "anchor", "kind": "run", "run": VT, "filled": True},
                            {"id": "around", "kind": "run", "run": CHUNKS, "filled": True, "binds": ["anchor.top_chunk"]}]),
                     step_fn=steps)
    argv = steps.calls[1][1]
    assert argv[argv.index("--idList") + 1] == "c0"


def test_a_value_with_spaces_and_shell_characters_stays_one_argument():
    steps = Steps({"vector-text": ANCHOR, "pattern10": {"command": "query graph pattern10", "rows": []}})
    run = "artmind query graph pattern10 --domain journal --documentName {anchor.top_doc} --compact"
    executor.execute(_plan([{"id": "anchor", "kind": "run", "run": VT, "filled": True},
                            {"id": "day", "kind": "run", "run": run, "filled": True, "binds": ["anchor.top_doc"]}]),
                     step_fn=steps)
    argv = steps.calls[1][1]
    assert argv[argv.index("--documentName") + 1] == "Day 1 & 2; rm"


def test_an_empty_binding_skips_the_node():
    steps = Steps({"vector-text": {"command": "query vector-text", "rows": []}})
    ex = executor.execute(_plan([{"id": "anchor", "kind": "run", "run": VT, "filled": True},
                                 {"id": "around", "kind": "run", "run": CHUNKS, "filled": True, "binds": ["anchor.top_chunk"]}]),
                          step_fn=steps)
    assert [c[0] for c in steps.calls] == ["anchor"]
    assert next(p for p in ex.path if p["id"] == "around")["skipped"] == "unbound"


def test_a_map_over_a_bound_list_expands_at_run_time():
    steps = Steps({"vector-text": ANCHOR, "pattern10": {"command": "query graph pattern10", "rows": [
        {"chunk": {"id": "x", "text": "t", "doc_id": "d"}}]}})
    do = {"id": "day", "kind": "run", "run": P10, "filled": True}
    ex = executor.execute(_plan([
        {"id": "anchor", "kind": "run", "run": VT, "filled": True},
        {"id": "dated", "kind": "map", "map": {"over": "anchor.window_docs", "over_bound": "anchor.window_docs",
                                               "as": "doc", "max": 6, "items": [], "expanded": [], "do": do}}]),
        step_fn=steps)
    called = [c for c in steps.calls if c[0].startswith("day.")]
    assert [c[0] for c in called] == ["day.1", "day.2"]  # d1, d2 within 3 days of the anchor; d9 is not
    assert called[0][1][called[0][1].index("--documentName") + 1] == "Day 1 & 2; rm"
    assert ex.facts["dated"]["rows"] == 2


def test_a_bound_map_respects_max():
    many = _rows(*[(f"d{i}", f"Day {i}", "2026-03-01") for i in range(10)])
    steps = Steps({"vector-text": many, "pattern10": {"command": "query graph pattern10", "rows": []}})
    do = {"id": "day", "kind": "run", "run": P10, "filled": True}
    executor.execute(_plan([
        {"id": "anchor", "kind": "run", "run": VT, "filled": True},
        {"id": "dated", "kind": "map", "map": {"over": "anchor.window_docs", "over_bound": "anchor.window_docs",
                                               "as": "doc", "max": 3, "items": [], "expanded": [], "do": do}}]),
        step_fn=steps)
    assert len([c for c in steps.calls if c[0].startswith("day.")]) == 3


def test_a_failed_producer_unbinds():
    class Fail(Steps):
        def __call__(self, argv, sid):
            self.calls.append((sid, list(argv)))
            return StepResult(sid, list(argv), 1, "boom", None, 1.0, 0)

    steps = Fail({})
    ex = executor.execute(_plan([{"id": "anchor", "kind": "run", "run": VT, "filled": True},
                                 {"id": "around", "kind": "run", "run": CHUNKS, "filled": True, "binds": ["anchor.top_chunk"]}]),
                          step_fn=steps)
    assert len(steps.calls) == 1 and next(p for p in ex.path if p["id"] == "around")["skipped"] == "unbound"


# ── review fixes ─────────────────────────────────────────────────────────────
def _search():
    needle = "artmind query vector-text --shape brief --domain journal --topK 10 q --compact"
    return {"program": [{"id": "needle", "kind": "run", "run": needle, "filled": True}], "guards": [],
            "route": {"id": "search"}, "replacements": {}}


def test_a_bound_map_whose_do_is_unfilled_does_not_run_and_goes_to_search():
    steps = Steps({"vector-text": ANCHOR, "pattern10": {"command": "query graph pattern10", "rows": []}})
    do = {"id": "day", "kind": "run", "run": "artmind query graph pattern10 --documentName {doc} --domain {nope}",
          "filled": False}
    plan = {"program": [
        {"id": "anchor", "kind": "run", "run": VT, "filled": True},
        {"id": "dated", "kind": "map", "map": {"over": "anchor.window_docs", "over_bound": "anchor.window_docs",
                                               "as": "doc", "max": 6, "items": [], "expanded": [], "do": do}}],
        "guards": [], "route": {"id": "episode"}, "replacements": {"search": _search()}}
    ex = executor.execute(plan, step_fn=steps)
    assert ex.replaced_by == "search"
    assert not any(c[0].startswith("day.") for c in steps.calls)
    assert not any("{nope}" in " ".join(c[1]) for c in steps.calls)


def test_a_bound_map_with_an_unfilled_do_and_no_replacement_is_skipped():
    steps = Steps({"vector-text": ANCHOR, "pattern10": {"command": "query graph pattern10", "rows": []}})
    do = {"id": "day", "kind": "run", "run": "artmind query graph pattern10 --documentName {doc} --domain {nope}",
          "filled": False}
    ex = executor.execute(_plan([
        {"id": "anchor", "kind": "run", "run": VT, "filled": True},
        {"id": "dated", "kind": "map", "map": {"over": "anchor.window_docs", "over_bound": "anchor.window_docs",
                                               "as": "doc", "max": 6, "items": [], "expanded": [], "do": do}}]),
        step_fn=steps)
    assert [c[0] for c in steps.calls] == ["anchor"]
    assert ex.unfilled is True


def test_a_binding_to_a_pick_node_reads_the_picked_leaves():
    a = "artmind query vector-text --shape brief --domain journal --topK 5 a --compact"
    b = "artmind query survey --shape brief --domain journal --topDocs 5 b --compact"
    steps = Steps({"vector-text": _rows(("da", "Doc A", "2026-03-01")),
                   "survey": _rows(("db", "Doc B", "2026-03-02")),
                   "chunks": {"command": "query chunks", "rows": []}})
    executor.execute(_plan([
        {"id": "p", "kind": "par", "children": [{"id": "a", "kind": "run", "run": a, "filled": True},
                                                  {"id": "b", "kind": "run", "run": b, "filled": True}]},
        {"id": "u", "kind": "pick", "pick": {"from": "p", "how": "union", "score": []}},
        {"id": "around", "kind": "run", "run": CHUNKS.replace("anchor", "u"), "filled": True, "binds": ["u.top_chunk"]}]),
        step_fn=steps)
    called = [c for c in steps.calls if c[0] == "around"]
    assert called and called[0][1][called[0][1].index("--idList") + 1] == "c0"


def test_a_bound_value_starting_with_a_dash_is_unbound():
    steps = Steps({"vector-text": _rows(("d1", "-x", "2026-03-01")), "pattern10": {"command": "p", "rows": []}})
    run = "artmind query graph pattern10 --domain journal --documentName {anchor.top_doc} --compact"
    ex = executor.execute(_plan([{"id": "anchor", "kind": "run", "run": VT, "filled": True},
                                 {"id": "day", "kind": "run", "run": run, "filled": True, "binds": ["anchor.top_doc"]}]),
                          step_fn=steps)
    assert [c[0] for c in steps.calls] == ["anchor"]
    assert next(p for p in ex.path if p["id"] == "day")["skipped"] == "unbound"


def test_a_bound_map_item_starting_with_a_dash_is_not_run():
    steps = Steps({"vector-text": _rows(("d1", "-x", "2026-03-01")), "pattern10": {"command": "p", "rows": []}})
    do = {"id": "day", "kind": "run", "run": P10, "filled": True}
    executor.execute(_plan([
        {"id": "anchor", "kind": "run", "run": VT, "filled": True},
        {"id": "dated", "kind": "map", "map": {"over": "anchor.window_docs", "over_bound": "anchor.window_docs",
                                               "as": "doc", "max": 6, "items": [], "expanded": [], "do": do}}]),
        step_fn=steps)
    assert not any("-x" in c[1] for c in steps.calls)


def test_a_replacement_does_not_reuse_cached_outputs():
    new = _rows(("dz", "Replaced Doc", "2026-05-01"))

    class Two(Steps):
        def __call__(self, argv, sid):
            self.calls.append((sid, list(argv)))
            payload = ANCHOR if (sid == "s1" and sum(c[0] == "s1" for c in self.calls) == 1) else (
                new if sid == "s1" else {"command": "query graph pattern10", "rows": []})
            return StepResult(sid, list(argv), 0, None, payload, 1.0, 10)

    run = "artmind query graph pattern10 --domain journal --documentName {s1.top_doc} --compact"
    repl = {"program": [{"id": "s1", "kind": "run", "run": VT, "filled": True},
                        {"id": "y", "kind": "run", "run": run, "filled": True, "binds": ["s1.top_doc"]}],
            "guards": []}
    plan = {"program": [{"id": "s1", "kind": "run", "run": VT, "filled": True},
                        {"id": "x", "kind": "run", "run": run, "filled": True, "binds": ["s1.top_doc"]}],
            "guards": [{"on": "x", "if": "rows == 0", "then": {"kind": "replace", "route": "other"}}],
            "route": {"id": "episode"}, "replacements": {"other": repl}}
    steps = Two({})
    executor.execute(plan, step_fn=steps)
    y = [c for c in steps.calls if c[0] == "y"][0][1]
    assert y[y.index("--documentName") + 1] == "Replaced Doc"
