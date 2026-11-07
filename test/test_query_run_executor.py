"""The executor over plan payloads (P3 spec §3.5–§3.8). Fake steps, invented payloads, no Neo4j."""

from artmind.query_run import executor
from artmind.query_run.capture import StepResult

NEEDLE = "artmind query vector-text --shape brief --domain bank --topK 10 fees --compact"
WIDE = "artmind query survey --shape brief --domain bank 'fees?' --compact"
P7 = "artmind query graph pattern7 --domain bank --searchTerm fees --limit 50 --shape brief --compact"


def _passage(doc, rel):
    return {"passage_id": f"p-{doc}-{rel}", "chunk_id": f"c-{doc}", "text": "t", "relevance": rel,
            "doc": {"id": doc, "title": doc}}


def _needle(pair, docs=3):
    rows = [_passage(f"d{i % docs}", 0.5) for i in range(10)]
    rows[0]["relevance"], rows[1]["relevance"] = pair
    return {"command": "query vector-text", "rows": rows}


SURVEY = {"command": "query survey", "documents": [{"doc_id": "s1", "title": "Rates"}]}


class Steps:
    """step_fn: payload by the command's third word (vector-text, survey, graph...) or an exception-like exit."""

    def __init__(self, by_word):
        self.by_word, self.calls = by_word, []

    def __call__(self, argv, step_id):
        self.calls.append((step_id, list(argv)))
        value = self.by_word[argv[1] if argv[1] != "graph" else argv[2]]
        if isinstance(value, int):
            return StepResult(step_id, list(argv), value, "boom", None, 1.0, 0)
        return StepResult(step_id, list(argv), 0, None, value, 1.0, 100)


def _search_plan(guards=None):
    return {"program": [
        {"id": "p", "kind": "par", "children": [{"id": "needle", "kind": "run", "run": NEEDLE, "filled": True},
                                                  {"id": "wide", "kind": "run", "run": WIDE, "filled": True}]},
        {"id": "u", "kind": "pick", "pick": {"from": "p", "how": "union", "score": []},
         "when": "needle.score_drop < NEEDLE_MARGIN or needle.distinct_documents >= NEEDLE_SPREAD"}],
        "guards": guards if guards is not None else [{"on": "u", "if": "distinct_documents == 0", "then": {"kind": "escalate"}}],
        "route": {"id": "search"}, "suggested_steps": [], "fallbacks": [], "replacements": {}}


def test_par_runs_both_and_pick_unions():
    steps = Steps({"vector-text": _needle((0.51, 0.5)), "survey": SURVEY})
    ex = executor.execute(_search_plan(), step_fn=steps)
    assert [c[0] for c in steps.calls] == ["needle", "wide"]
    assert ex.facts["u"]["distinct_documents"] == 4 and ex.escalate is None
    assert ex.answer_ids == ["needle", "wide"] and ex.executed == 2


def test_a_clear_needle_short_circuits_the_survey():
    steps = Steps({"vector-text": _needle((0.9, 0.5)), "survey": SURVEY})
    ex = executor.execute(_search_plan(), step_fn=steps)
    assert [c[0] for c in steps.calls] == ["needle"]
    assert next(p for p in ex.path if p["id"] == "wide")["skipped"] == "short_circuit"
    assert ex.answer_ids == ["needle"]


def test_a_guard_escalates_with_its_predicate():
    steps = Steps({"vector-text": {"command": "query vector-text", "rows": []}, "survey": {"command": "query survey", "documents": []}})
    ex = executor.execute(_search_plan(), step_fn=steps)
    assert ex.escalate["reason"] == "guard" and ex.escalate["node"] == "u"
    assert ex.escalate["predicate"] == "distinct_documents == 0"


def test_replace_runs_the_replacement_once():
    plan = {"program": [{"id": "s1", "kind": "run", "run": P7, "filled": True}],
            "guards": [{"on": "s1", "if": "rows == 0", "then": {"kind": "replace", "route": "search"}}],
            "route": {"id": "existence"}, "suggested_steps": [], "fallbacks": [],
            "replacements": {"search": _search_plan()}}
    steps = Steps({"pattern7": {"command": "query graph pattern7", "rows": []},
                   "vector-text": _needle((0.51, 0.5)), "survey": SURVEY})
    ex = executor.execute(plan, step_fn=steps)
    assert ex.replaced_by == "search" and [c[0] for c in steps.calls] == ["s1", "needle", "wide"]
    assert "s1" not in ex.results and ex.escalate is None
    assert any(p.get("kind") == "replace" for p in ex.path)


def test_an_unfilled_program_runs_the_search_instead():
    plan = {"program": [{"id": "s1", "kind": "run", "run": "artmind query entity-context --entityId {entity_id}", "filled": False}],
            "guards": [], "route": {"id": "entity_profile"}, "suggested_steps": [], "fallbacks": [],
            "replacements": {"search": _search_plan()}}
    steps = Steps({"vector-text": _needle((0.51, 0.5)), "survey": SURVEY})
    ex = executor.execute(plan, step_fn=steps)
    assert ex.replaced_by == "search" and ex.path[0]["reason"] == "unfilled"


def test_an_optional_unfilled_node_is_skipped_quietly():
    plan = {"program": [{"id": "s1", "kind": "run", "run": "artmind query domains-overview --compact", "filled": True},
                        {"id": "s2", "kind": "run", "run": "x {class}", "filled": False, "optional": True}],
            "guards": [], "route": {"id": "inventory"}, "replacements": {}}
    steps = Steps({"domains-overview": {"command": "query domains-overview", "rows": [{"domain": "bank"}]}})
    ex = executor.execute(plan, step_fn=steps)
    assert ex.replaced_by is None and ex.escalate is None
    assert next(p for p in ex.path if p["id"] == "s2")["skipped"] == "unfilled"


def test_a_when_on_a_run_node_reads_earlier_facts():
    plan = {"program": [{"id": "s1", "kind": "run", "run": P7, "filled": True},
                        {"id": "s2", "kind": "run", "run": NEEDLE, "filled": True, "when": "s1.rows == 0"}],
            "guards": [], "route": {"id": "path"}, "replacements": {}}
    steps = Steps({"pattern7": {"command": "query graph pattern7", "rows": [{"id": "e1"}]}, "vector-text": _needle((0.5, 0.4))})
    ex = executor.execute(plan, step_fn=steps)
    assert [c[0] for c in steps.calls] == ["s1"]
    assert next(p for p in ex.path if p["id"] == "s2")["skipped"] == "when"


def test_a_page_node_reruns_with_a_larger_limit():
    plan = {"program": [{"id": "s1", "kind": "run", "run": P7, "filled": True},
                        {"id": "more", "kind": "page", "page": {"until": "rows_total", "k": None, "from": "s1"}, "when": "s1.truncated"}],
            "guards": [{"on": "more", "if": "truncated", "then": {"kind": "escalate"}}], "route": {"id": "enumeration"}, "replacements": {}}
    first = {"command": "query graph pattern7", "rows": [{"id": f"e{i}"} for i in range(50)], "rows_total": 120, "truncated": True}
    full = {"command": "query graph pattern7", "rows": [{"id": f"e{i}"} for i in range(120)], "rows_total": 120, "truncated": False}
    calls = iter([first, full])

    def step_fn(argv, step_id):
        return StepResult(step_id, list(argv), 0, None, next(calls), 1.0, 10)

    ex = executor.execute(plan, step_fn=step_fn)
    assert ex.results["s1"].argv[ex.results["s1"].argv.index("--limit") + 1] == "120"
    assert ex.facts["more"]["truncated"] is False and ex.escalate is None and ex.executed == 2


def test_a_failed_step_continues_and_all_failed_is_step_error():
    steps = Steps({"vector-text": 1, "survey": SURVEY})
    ex = executor.execute(_search_plan(guards=[]), step_fn=steps)
    assert ex.results["needle"].exit_code == 1 and ex.escalate is None  # the survey still produced evidence
    steps = Steps({"vector-text": 3, "survey": 3})
    ex = executor.execute(_search_plan(guards=[]), step_fn=steps)
    assert ex.escalate["reason"] == "step_error" and ex.exit_code == 3


def test_the_step_limit_stops_the_run():
    steps = Steps({"vector-text": _needle((0.51, 0.5)), "survey": SURVEY})
    ex = executor.execute(_search_plan(guards=[]), step_fn=steps, max_steps=1)
    assert ex.executed == 1 and ex.escalate["reason"] == "limit"


def test_the_deadline_stops_before_the_next_step():
    ticks = iter([0.0, 0.0, 50.0, 50.0, 50.0])
    steps = Steps({"vector-text": _needle((0.51, 0.5)), "survey": SURVEY})
    ex = executor.execute(_search_plan(guards=[]), step_fn=steps, deadline_s=20, clock=lambda: next(ticks))
    assert ex.executed == 1 and ex.escalate["reason"] == "deadline" and ex.partial is True


def test_no_program_is_no_lookup():
    ex = executor.execute({"program": [], "answer_from": "previous", "action": None}, step_fn=Steps({}))
    assert ex.escalate == {"reason": "no_lookup", "answer_from": "previous", "action": None, "suggested": []}


def test_a_map_runs_each_expanded_item_with_unique_ids():
    do = {"id": "version", "kind": "run", "run": "artmind query graph pattern10 --documentName {doc}", "filled": True}
    exp = [{**do, "run": f"artmind query graph pattern10 --documentName d{i} --compact"} for i in (1, 2)]
    plan = {"program": [{"id": "versions", "kind": "map", "map": {"over": "family_documents", "as": "doc", "max": 10,
                                                                   "items": ["d1", "d2"], "do": do, "expanded": exp}}],
            "guards": [], "route": {"id": "document"}, "replacements": {}}
    steps = Steps({"pattern10": {"command": "query graph pattern10", "rows": [{"chunk": {"id": "c", "text": "t", "doc_id": "d"}}]}})
    ex = executor.execute(plan, step_fn=steps)
    assert [c[0] for c in steps.calls] == ["version.1", "version.2"] and ex.facts["versions"]["rows"] == 2


def test_an_unmet_need_skips_the_node():
    plan = {"program": [{"id": "s1", "kind": "run", "run": "artmind query insights list --domain j --compact", "filled": True,
                         "needs_unmet": True}], "guards": [], "route": {"id": "insights"}, "replacements": {}}
    ex = executor.execute(plan, step_fn=Steps({}))
    assert ex.path[0]["skipped"] == "needs" and ex.escalate["reason"] == "no_evidence"


def test_unsupported_guard_actions_escalate():
    plan = _search_plan(guards=[{"on": "u", "if": "rows >= 0", "then": {"kind": "retry_with", "slots": {"x": "y"}}}])
    steps = Steps({"vector-text": _needle((0.51, 0.5)), "survey": SURVEY})
    ex = executor.execute(plan, step_fn=steps)
    assert ex.escalate["reason"] == "guard" and ex.escalate["action"] == "retry_with"


def test_a_refused_command_is_recorded_not_run():
    plan = {"program": [{"id": "s1", "kind": "run", "run": "artmind ingest sync x", "filled": True}],
            "guards": [], "route": {"id": "x"}, "replacements": {}}
    ex = executor.execute(plan, step_fn=Steps({}))
    assert ex.results["s1"].error == "not_allowed" and ex.executed == 0


def test_page_limit_matches_the_command():
    from artmind import graph_query
    assert executor.PAGE_LIMITS["query graph pattern7"] == graph_query.PATTERN7_MAX_LIMIT
