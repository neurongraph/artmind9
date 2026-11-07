"""The non-search routes carry a brief survey leg (`wide`) beside their own first steps (candidate A of
2026-11-07-b11-entity-routes-text-proposal.md). Synthetic data only; fixtures from test_query_plan_planner."""

import pytest

from artmind.query_plan import candidates, slots, program as prog, routes
from artmind.query_run import executor
from artmind.query_run.capture import StepResult
from test_query_plan_planner import FakeClassifier, _answer, _plan, _use, world  # noqa: F401

WIDE_CMD = "artmind query survey --shape brief --domain {domains} {question} --compact"

#: (route, program kind) -> a fixture question that fills it. None kind = the route's only program.
CHANGED = [
    ("entity_profile", "one_step", "Tell me about Lakeport"),
    ("entity_profile", "entity_visits", "Tell me about the visits to Lakeport"),
    ("entity_history", "history", "How did Ada change over time?"),
    ("time_history", "history", "Which version of policy_alpha.md applied before 2026-03-01?"),
    ("conflicts", "one_step", "Do the reports contradict each other?"),
    ("hierarchy", "one_step", "Who sits under Ada?"),
    ("path", "one_step", "How are Ada and Lakeport related?"),
    ("aggregate", "one_step", "How often did the team meet?"),
    ("aggregate", "kinship_aggregate", "How many times was Lakeport visited?"),
    ("superlative", "one_step", "What is the biggest thing?"),
    ("enumeration", "enumeration", "List every recorded meeting"),
    ("enumeration", "draw_connected", "List all the visits to Lakeport"),
    ("existence", "one_step", "Did I visit Lakeport again?"),
    ("document", "one_step", "What does policy_alpha.md say?"),
    ("insights", "one_step", "What changed lately?"),
    ("filing", "one_step", "Which notes are tagged travel?"),
]
UNTOUCHED = [("existence", "existence_concept"), ("existence", "absence"), ("document", "latest"),
             ("document", "topic_latest"), ("structured", None), ("search", None), ("survey", None)]


def _route(rid):
    return next(r for r in routes.load_routes() if r.id == rid)


def _walk(nodes):
    for n in nodes:
        yield n
        yield from _walk(n.get("children") or [])
        if n.get("map"):
            yield from _walk([n["map"]["do"]])


def test_every_changed_choice_has_a_wide_survey_in_a_par_and_the_limits_hold():
    for rid in {r for r, _, _ in CHANGED}:
        for choice in _route(rid).programs:
            if (rid, choice.kind) in UNTOUCHED:
                continue
            wides = [n for n in prog.run_nodes(choice.program) if n.id == "wide"]
            assert len(wides) == 1 and wides[0].run == WIDE_CMD, (rid, choice.kind)
            assert any(n.kind == "par" and any(c.id == "wide" for c in n.children) for n in choice.program), (rid, choice.kind)
            assert len(prog.run_nodes(choice.program)) <= prog.MAX_WRITTEN_RUN_NODES
            assert prog.executed_run_calls(choice.program) <= prog.MAX_EXECUTED_RUN_CALLS


@pytest.mark.parametrize("rid, kind", UNTOUCHED[:4])
def test_the_programs_left_alone_have_no_survey_leg(rid, kind):
    choice = next(c for c in _route(rid).programs if c.kind == kind)
    assert not any(n.id == "wide" for n in prog.run_nodes(choice.program))


@pytest.mark.parametrize("rid", ["structured"])
def test_structured_is_untouched(rid):
    assert not any("survey" in t for t in _route(rid).steps)


@pytest.fixture
def common_words(monkeypatch):
    monkeypatch.setattr(slots, "rare_term", lambda *a, **k: False)


@pytest.fixture
def named_documents(monkeypatch):
    monkeypatch.setattr(candidates, "document_names", lambda domains: ["policy_alpha.md"])


@pytest.mark.parametrize("rid, kind, question", CHANGED, ids=[f"{r}-{k}" for r, k, _ in CHANGED])
def test_the_plan_carries_a_filled_wide_survey_beside_the_first_step(world, named_documents, common_words, monkeypatch, rid, kind, question):
    world["views"] = []
    world["entities"]["Lakeport"] = [
        {"_id": "l1", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]},
        {"_id": "l2", "name": "Lakeport", "entity_class": "LOCATION", "matched_by": ["exact"]}] if kind == "kinship_aggregate" \
        else world["entities"]["Lakeport"]
    _use(monkeypatch, FakeClassifier(override={"route": _answer(rid, 0.95)}))
    result = _plan(question)
    assert result["route"]["id"] == rid and result["program_kind"] == kind, result["program_kind"]
    nodes = list(_walk(result["program"]))
    wide = next(n for n in nodes if n["id"] == "wide")
    assert wide["filled"] is True and wide["run"].startswith("artmind query survey --shape brief --domain journal ")
    par = next(n for n in nodes if n["kind"] == "par" and any(c["id"] == "wide" for c in n["children"]))
    assert len(par["children"]) >= 2 and par["children"][-1]["id"] == "wide"
    assert par["children"][0]["id"] != "wide" and par["children"][0].get("filled", True) is not False
    assert any(s["cmd"].startswith("artmind query survey --shape brief") for s in result["steps"] + result["suggested_steps"])


def test_the_conditional_steps_stay_after_the_par(world, monkeypatch):
    world["views"] = []
    _use(monkeypatch, FakeClassifier(override={"route": _answer("path", 0.95)}))
    program = _plan("How are Ada and Lakeport related?")["program"]
    assert [n["id"] for n in program] == ["p", "s2", "s3"]
    assert [c["id"] for c in program[0]["children"]] == ["s1", "wide"]
    assert program[1]["when"] == program[2]["when"] == "s1.rows == 0"


def test_a_route_that_cannot_fill_its_own_step_is_not_left_with_the_survey_alone(world, monkeypatch):
    # existence needs entity_name; unfilled -> low plan with the search suggestions, not a survey-only step
    result = _plan("Did I go to the gym again?")
    assert result["steps"] == [] and result["confidence"]["weakest"] == "unfilled"


# ── executor ─────────────────────────────────────────────────────────────────

CTX = "artmind query entity-context --domain bank --entityId e1 --shape brief --compact"
WIDE = "artmind query survey --shape brief --domain bank 'tell me about e1' --compact"
SEARCH_NEEDLE = "artmind query vector-text --shape brief --domain bank --topK 10 e1 --compact"


def _profile_plan():
    return {"program": [
        {"id": "p", "kind": "par", "children": [{"id": "s1", "kind": "run", "run": CTX, "filled": True},
                                                  {"id": "wide", "kind": "run", "run": WIDE, "filled": True}]},
        {"id": "u", "kind": "pick", "pick": {"from": "p", "how": "union", "score": []}}],
        "guards": [{"on": "s1", "if": "rows == 0", "then": {"kind": "replace", "route": "search"}}],
        "route": {"id": "entity_profile"}, "suggested_steps": [], "fallbacks": [],
        "replacements": {"search": {"program": [
            {"id": "p", "kind": "par", "children": [{"id": "needle", "kind": "run", "run": SEARCH_NEEDLE, "filled": True},
                                                      {"id": "wide", "kind": "run", "run": WIDE, "filled": True}]},
            {"id": "u", "kind": "pick", "pick": {"from": "p", "how": "union", "score": []}}], "guards": []}}}


class Steps:
    def __init__(self, ctx_rows):
        self.ctx_rows, self.calls = ctx_rows, []

    def __call__(self, argv, step_id):
        self.calls.append(step_id)
        if argv[1] == "entity-context":
            payload = {"command": "query entity-context", "rows": self.ctx_rows}
        elif argv[1] == "survey":
            payload = {"command": "query survey", "documents": [{"doc_id": "s1", "title": "Rates"}]}
        else:
            payload = {"command": "query vector-text", "rows": [
                {"passage_id": "pn", "chunk_id": "cn", "text": "t", "relevance": 0.6, "doc": {"id": "dn", "title": "dn"}}]}
        return StepResult(step_id, list(argv), 0, None, payload, 1.0, 100)


def test_the_profile_bundle_holds_the_entity_evidence_beside_the_survey():
    row = {"entity": {"id": "e1", "name": "Ada"}, "connections": [], "chunks": [
        {"chunk_id": "c1", "text": "Ada visited", "doc": {"id": "d1", "title": "d1"}}]}
    steps = Steps([row])
    ex = executor.execute(_profile_plan(), step_fn=steps)
    assert steps.calls == ["s1", "wide"] and ex.answer_ids == ["s1", "wide"] and ex.escalate is None
    from artmind.query_run import bundle
    printed, _ = bundle.build({"route": {"id": "entity_profile"}, "scope": None}, ex, budget=24000)
    ids = {d["doc_id"] for d in printed["documents"]}
    assert {"d1", "s1"} <= ids
    assert any(e["from"] == ["s1"] for e in printed["evidence"])  # the entity's chunk evidence is ranked in


def test_no_entity_rows_still_replaces_with_search():
    steps = Steps([])
    ex = executor.execute(_profile_plan(), step_fn=steps)
    assert ex.replaced_by == "search"
    assert steps.calls == ["s1", "needle", "wide"]  # the guard fires right after s1; the first wide never ran
    assert ex.answer_ids == ["needle", "wide"]
