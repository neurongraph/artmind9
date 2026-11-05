"""routes.yaml and its loader (spec 2026-10-22 §4.3)."""

import shlex
from pathlib import Path

import click
import pytest

from artmind.query_plan import routes

EXPECTED_IDS = {
    "existence", "enumeration", "aggregate", "superlative", "entity_profile", "entity_history",
    "time_history", "conflicts", "hierarchy", "path", "document", "inventory", "procedure",
    "structured", "insights", "filing", "survey", "search",
}
SKILL_DIR = Path(routes.__file__).resolve().parents[1] / "skills" / "artmind-query"


def test_the_packaged_routes_load():
    loaded = routes.load_routes()
    assert {r.id for r in loaded} == EXPECTED_IDS
    assert all(r.evidence for r in loaded)


_SENTINEL = "@@placeholder@@"


def _resolve(template: str):
    """Resolve a template against the Click tree; placeholder values become a sentinel."""
    from artmind.cli import cli

    filled = routes.render(template, {name: _SENTINEL for name in (*routes.ALL_PLACEHOLDERS, "phrase", "twin", "doc")})
    tokens = shlex.split(filled)
    assert tokens[0] == "artmind", template
    command, i = cli, 1
    while i < len(tokens) and hasattr(command, "commands") and tokens[i] in command.commands:
        command = command.commands[tokens[i]]
        i += 1
    return command, tokens[i:]


def _parse_args(command, rest):
    """Split the tokens after the command into {option: value or None} and positionals."""
    by_name = {opt: param for param in command.params for opt in getattr(param, "opts", [])}
    options, positionals, i = {}, [], 0
    while i < len(rest):
        token = rest[i]
        if token.startswith("--"):
            param = by_name.get(token)
            takes_value = param is not None and not getattr(param, "is_flag", False)
            options[token] = rest[i + 1] if takes_value and i + 1 < len(rest) else None
            i += 2 if takes_value else 1
        else:
            positionals.append(token)
            i += 1
    return by_name, options, positionals


@pytest.mark.parametrize("route", routes.load_routes(), ids=lambda r: r.id)
def test_every_template_names_a_real_command_and_real_options(route):
    for template in [*route.steps, *(t for v in route.variants.values() for t in v), *route.optional_steps, *(f.cmd for f in route.fallbacks)]:
        command, rest = _resolve(template)
        assert not hasattr(command, "commands") or getattr(command, "invoke_without_command", False), template
        by_name, options, positionals = _parse_args(command, rest)
        assert set(options) <= set(by_name), f"{template}: unknown {sorted(set(options) - set(by_name))}"
        for param in command.params:
            if isinstance(param, click.Argument):
                if param.required:
                    assert positionals, f"{template}: missing required argument {param.name}"
            elif param.required:
                assert set(param.opts) & set(options), f"{template}: missing required {param.opts}"
        for opt, value in options.items():
            param = by_name[opt]
            if value is None or value == _SENTINEL:
                continue
            if isinstance(param.type, (click.Choice, click.IntRange)):
                param.type.convert(value, param, None)  # raises BadParameter when invalid


@pytest.mark.parametrize("route", routes.load_routes(), ids=lambda r: r.id)
def test_every_read_file_exists(route):
    for ref in route.read:
        assert (SKILL_DIR / ref).is_file(), ref


def _one(**over):
    base = {"id": "r1", "description": "A shape.", "triggers": ["x"], "needs": [],
            "steps": ["artmind query vector-text --domain {domains} {question} --compact"],
            "fallbacks": [], "read": [], "evidence": ["S1"]}
    return {"version": 1, "routes": [{**base, **over}]}


@pytest.mark.parametrize("data, message", [
    (_one(id="view"), "reserved"),
    (_one(id="Bad-Id"), "snake_case"),
    (_one(steps=["artmind query vector-text --domain {nope} --compact"]), "unknown placeholder"),
    (_one(steps=["artmind query vector-text --domain {domains}"]), "--compact"),
    (_one(variants={"mystery": ["artmind query vector-text --domain {domains} --compact"]}), "unknown store kind"),
    (_one(variants={"graph": ["artmind query vector-text --domain {domains}"]}), "--compact"),
    (_one(variants={"graph": ["artmind query vector-text --domain {nope} --compact"]}), "unknown placeholder"),
    (_one(variants={"graph": []}), "must not be empty"),
    (_one(variants=["x"]), "variants"),
    (_one(optional_steps=["artmind query vector-text --domain {domains}"]), "--compact"),
    (_one(optional_steps=["artmind query vector-text --domain {nope} --compact"]), "unknown placeholder"),
    (_one(evidence=[]), "must not be empty"),
    (_one(evidence=["Q7"]), "evidence"),
    (_one(fallbacks=[{"when": "later", "cmd": "artmind query chunks --compact"}]), "fallbacks"),
    (_one(needs=["magic"]), "unknown needs"),
    (_one(triggers=["on {when}"]), "unknown slot"),
    (_one(description="x" * 301), "description"),
    (_one(steps=["artmind query vector-text --domain {entity1 --compact"]), "stray brace"),
    (_one(steps=["artmind query vector-text --domain {domains} {Question} --compact"]), "stray brace"),
    (_one(triggers=["on {entity1"]), "stray brace"),
    (_one(fallbacks=[{"when": ["a"], "cmd": "artmind query chunks --compact"}]), "fallbacks"),
    ({**_one(), "version": True}, "version"),
])
def test_rule_violations_are_named(data, message):
    with pytest.raises(routes.RouteError, match=message):
        routes.parse_routes(data)


def test_duplicate_ids_are_refused():
    data = _one()
    data["routes"].append(dict(data["routes"][0]))
    with pytest.raises(routes.RouteError, match="duplicate"):
        routes.parse_routes(data)


def test_render_quotes_values_and_refuses_missing_ones():
    template = "artmind query vector-text --domain {domains} {question} --compact"
    assert routes.render(template, {"domains": "a,b", "question": "what's new?"}) == (
        "artmind query vector-text --domain a,b 'what'\"'\"'s new?' --compact"
    )
    assert routes.render(template, {"domains": "a", "question": ""}) is None


def test_render_does_not_re_expand_or_execute_values():
    template = "artmind query vector-text --domain {domains} {question} --compact"
    nested = routes.render(template, {"domains": "a", "question": "{question}"})
    assert nested == "artmind query vector-text --domain a '{question}' --compact"
    hostile = routes.render(template, {"domains": "a", "question": "$(rm -rf /)"})
    assert hostile == "artmind query vector-text --domain a '$(rm -rf /)' --compact"


@pytest.mark.parametrize("value", ["--help", "   ", "-x"])
def test_render_refuses_blank_or_option_like_values(value):
    template = "artmind query vector-text --domain {domains} {question} --compact"
    assert routes.render(template, {"domains": "a", "question": value}) is None


def test_valid_variants_parse_and_steps_for_picks_them():
    cmd = "artmind query text2sql --domain {domains} {question} --compact"
    (route,) = routes.parse_routes(_one(variants={"analytical": [cmd]}))
    assert route.variants == {"analytical": (cmd,)}
    assert route.steps_for("analytical") == (cmd,)
    assert route.steps_for("hybrid") == route.steps and route.steps_for(None) == route.steps


def test_every_placeholder_in_a_route_is_one_the_planner_supplies():
    from artmind.query_plan import planner

    supplied = set(planner._context(["d"], "q", [], None, []))
    used = set()
    for route in routes.load_routes():
        templates = [*route.steps, *(t for v in route.variants.values() for t in v), *route.optional_steps, *(f.cmd for f in route.fallbacks)]
        for template in templates:
            used |= routes.placeholders(template)
    assert used <= supplied, sorted(used - supplied)
    assert routes.PLACEHOLDERS <= supplied, sorted(routes.PLACEHOLDERS - supplied)


def test_optional_steps_parse():
    cmd = "artmind query vector-text --domain {domains} --compact"
    (route,) = routes.parse_routes(_one(optional_steps=[cmd]))
    assert route.optional_steps == (cmd,)
    assert routes.parse_routes(_one())[0].optional_steps == ()


# ── routes v2: plan programs (spec 2026-11-05 §5) ────────────────────────────

from artmind.query_plan import program as prog  # noqa: E402

RETRIEVAL_COMMANDS = ("query survey", "query vector-text", "query chunks", "query entity-context")
NEEDLE = "artmind query vector-text --shape brief --domain {domains} --topK 10 {search_term} --compact"
WIDE = "artmind query survey --shape brief --domain {domains} {question} --compact"
BRIEF_VECTOR = "artmind query vector-text --shape brief --domain {domains} {question} --compact"


def _v2(**over):
    base = {"id": "p1", "description": "A shape.", "triggers": ["x"], "needs": [], "read": [], "evidence": ["S1"]}
    return {"version": 2, "routes": [{**base, **over}]}


SURVEY_PROGRAM = [
    {"id": "p", "par": [{"id": "needle", "run": NEEDLE}, {"id": "wide", "run": WIDE}]},
    {"id": "u", "pick": {"from": "p", "how": "union"}, "when": "not needle.score_drop >= NEEDLE_MARGIN"},
]
SURVEY_GUARDS = [
    {"on": "wide", "if": "truncated", "then": {"page": {"until": "k", "k": 50}}},
    {"on": "u", "if": "distinct_documents == 0", "then": "escalate"},
]


def _program_route(program=None, guards=None):
    data = _v2(program=SURVEY_PROGRAM if program is None else program)
    data["routes"][0]["guards"] = SURVEY_GUARDS if guards is None else guards
    return data


def test_the_survey_example_parses_as_a_program_route():
    (route,) = routes.parse_routes(_program_route())
    assert route.authored_program
    assert [n.id for n in route.program] == ["p", "u"]
    assert route.steps == (NEEDLE, WIDE)  # run nodes flattened, for v1 readers
    assert route.fallbacks == () and route.variants == {} and route.optional_steps == ()
    assert route.steps_for("graph") == route.steps
    negated = route.program[1].when.ast
    assert isinstance(negated, prog.Not)
    assert negated.operand == prog.Cmp("score_drop", "needle", ">=", 0.03, "NEEDLE_MARGIN")
    assert [g.then.kind for g in route.guards] == ["page", "escalate"]
    assert route.guards[0].then.page == prog.PageSpec("k", 50)
    assert route.guards[0].when.ast == prog.Cmp("truncated", None, "==", True)


def test_versions_one_and_two_are_accepted_and_others_refused():
    assert routes.parse_routes(_one())[0].id == "r1"
    assert routes.parse_routes({**_one(), "version": 2})[0].id == "r1"
    with pytest.raises(routes.RouteError, match="version"):
        routes.parse_routes({**_one(), "version": 3})


def test_the_packaged_file_is_version_two():
    import yaml

    assert yaml.safe_load(routes.ROUTES_FILE.read_text())["version"] == 2


def test_program_needs_version_two_and_excludes_the_v1_keys():
    with pytest.raises(routes.RouteError, match="version: 2"):
        routes.parse_routes({**_program_route(), "version": 1})
    for key, value in (("steps", [BRIEF_VECTOR]), ("fallbacks", []), ("variants", {}), ("optional_steps", [])):
        data = _program_route()
        data["routes"][0][key] = value
        with pytest.raises(routes.RouteError, match="exactly one|not both"):
            routes.parse_routes(data)
    with pytest.raises(routes.RouteError, match="guards"):
        routes.parse_routes(_one(guards=[]))
    with pytest.raises(routes.RouteError, match="steps"):
        routes.parse_routes(_v2())  # neither form


def _run(i, template=BRIEF_VECTOR):
    return {"id": f"r{i}", "run": template}


def _bad(program, message, guards=None):
    with pytest.raises(routes.RouteError, match=message):
        routes.parse_routes(_program_route(program=program, guards=guards or []))


def test_each_node_kind_is_accepted():
    inner = "artmind query graph pattern7 --domain {domains} --searchTerm {twin} --compact"
    program = [
        {"id": "a", "run": BRIEF_VECTOR},
        {"id": "m", "map": {"over": "twin_ids", "as": "twin", "max": 3, "do": {"id": "mm", "run": inner}}},
        {"id": "pk", "pick": {"from": "m", "how": "best_of", "score": ["max_relevance > 0.5", "rows >= 1"]}},
        {"id": "pg", "page": {"from": "a", "until": "rows_total"}, "when": "a.rows_total > 10 and not a.truncated"},
        {"id": "f", "pick": {"from": "m", "how": "first_ok"}},
    ]
    (route,) = routes.parse_routes(_program_route(program, guards=[]))
    assert [n.kind for n in route.program] == ["run", "map", "pick", "page", "pick"]
    assert route.program[1].map.max == 3 and route.program[1].map.do.id == "mm"
    assert len(route.program[2].pick.score) == 2
    assert route.steps == (BRIEF_VECTOR, inner)


def test_map_max_defaults_to_ten():
    template = "artmind query chunks --shape brief --ids {y} --compact"
    program = [{"id": "m", "map": {"over": "years", "as": "y", "do": _run(1, template)}}]
    (route,) = routes.parse_routes(_program_route(program, guards=[]))
    assert route.program[0].map.max == 10


@pytest.mark.parametrize("program, message", [
    ([{"id": "a"}], "exactly one"),
    ([{"id": "a", "run": BRIEF_VECTOR, "par": []}], "exactly one"),
    ([{"id": "a", "run": BRIEF_VECTOR, "extra": 1}], "unknown key"),
    ([{"run": BRIEF_VECTOR}], "snake_case"),
    ([{"id": "Bad", "run": BRIEF_VECTOR}], "snake_case"),
    ([], "non-empty"),
    ([_run(1), _run(1)], "duplicate node id"),
    ([{"id": "a", "run": "artmind query vector-text --domain {domains} {question}"}], "--compact"),
    ([{"id": "a", "run": "artmind query vector-text --domain {nope} --compact"}], "unknown placeholder"),
    ([{"id": "a", "run": "echo hi --compact"}], "start"),
    ([{"id": "a", "par": []}], "non-empty"),
    ([{"id": "a", "par": [{"id": "b", "pick": {"from": "a", "how": "union"}}]}], "no run node"),
    ([_run(1), {"id": "p", "pick": {"from": "ghost", "how": "union"}}], "not an earlier node"),
    ([_run(1), {"id": "p", "pick": {"from": "r1", "how": "union"}}], "par or map"),
    ([{"id": "q", "par": [_run(1)]}, {"id": "p", "pick": {"from": "q", "how": "voting"}}], "pick.how"),
    ([{"id": "q", "par": [_run(1)]}, {"id": "p", "pick": {"from": "q", "how": "union", "score": ["rows > 1"]}}], "best_of"),
    ([_run(1), {"id": "g", "page": {"from": "ghost", "until": "rows_total"}}], "not an earlier node"),
    ([_run(1), {"id": "g", "page": {"from": "r1", "until": "never"}}], "until"),
    ([_run(1), {"id": "g", "page": {"from": "r1", "until": "k"}}], "k must be"),
    ([_run(1), {"id": "g", "page": {"from": "r1", "until": "k", "k": 0}}], "k must be"),
    ([_run(1), {"id": "g", "page": {"from": "r1", "until": "rows_total", "k": 5}}], "only goes with"),
    ([_run(1), {"id": "g", "page": {"until": "rows_total"}}], "from"),
    ([{"id": "q", "par": [_run(1)]}, {"id": "g", "page": {"from": "q", "until": "rows_total"}}], "run node"),
    ([_run(1), {"id": "m", "map": {"over": "twin_ids", "as": "y"}}], "map must be"),
    ([{"id": "m", "map": {"over": "X!", "as": "y", "do": _run(1)}}], "snake_case"),
    ([{"id": "m", "map": {"over": "twin_ids", "as": "y", "max": 41, "do": _run(1)}}], "max"),
    ([{"id": "m", "map": {"over": "twin_ids", "as": "y", "max": 0, "do": _run(1)}}], "max"),
    ([{"id": "m", "map": {"over": "twin_ids", "as": "y", "max": True, "do": _run(1)}}], "max"),
    ([{"id": "m", "map": {"over": "twin_ids", "as": "y", "do": _run(1, "artmind query vector-text --domain {domains} {z} --compact")}}], "unknown placeholder"),
])
def test_program_rule_violations_are_named(program, message):
    _bad(program, message)


def test_the_map_variable_is_a_placeholder_only_inside_its_do():
    template = "artmind query vector-text --domain {domains} {phrasing} --compact"
    inside = [{"id": "m", "map": {"over": "phrasings", "as": "phrasing", "do": _run(1, template)}}]
    routes.parse_routes(_program_route(inside, guards=[]))
    outside = [{"id": "m", "map": {"over": "phrasings", "as": "phrasing", "do": _run(1)}}, _run(2, template)]
    _bad(outside, "unknown placeholder")


def test_v2_templates_may_use_the_new_placeholders_but_v1_steps_may_not():
    for name in sorted(routes.V2_PLACEHOLDERS):
        template = f"artmind query vector-text --shape brief --domain {{domains}} {{{name}}} --compact"
        routes.parse_routes(_program_route([{"id": "a", "run": template}], guards=[]))
        with pytest.raises(routes.RouteError, match="unknown placeholder"):
            routes.parse_routes(_one(steps=[template]))
    assert routes.V2_PLACEHOLDERS.isdisjoint(routes.PLACEHOLDERS)


def _nested(levels):
    node = _run(1)
    for i in range(levels - 1):
        node = {"id": f"n{i}", "par": [node]}
    return [node]


def test_nesting_is_limited_to_three_levels():
    routes.parse_routes(_program_route(_nested(3), guards=[]))
    _bad(_nested(4), "nesting deeper than 3")
    deep_map = [{"id": "m", "map": {"over": "twin_ids", "as": "y", "do": {"id": "p", "par": [{"id": "q", "par": [_run(1)]}]}}}]
    _bad(deep_map, "nesting deeper than 3")


def test_run_node_limits_count_written_nodes_and_map_expansion():
    twelve = [_run(i) for i in range(12)]
    routes.parse_routes(_program_route(twelve, guards=[]))
    _bad([*twelve, _run(99)], "13 run nodes")
    # a map body is written once however big max is, but executes max times
    big = [{"id": "m", "map": {"over": "twin_ids", "as": "y", "max": 40, "do": _run(1)}}]
    (route,) = routes.parse_routes(_program_route(big, guards=[]))
    assert prog.executed_run_calls(route.program) == 40
    two_inside = [{"id": "m", "map": {"over": "twin_ids", "as": "y", "max": 40, "do": {"id": "p", "par": [_run(1), _run(2)]}}}]
    _bad(two_inside, "80 run commands")


@pytest.mark.parametrize("text", [
    "rows == 0", "rows != 3", "rows < 2", "rows <= 2", "rows > 2", "rows >= 2", "truncated", "not truncated",
    "truncated == true", "stale == false", "wide.rows_total > 50.5", "rows == 0 and not truncated",
    "needle.score_drop >= NEEDLE_MARGIN", "view_status == stale", "rows > -1", "not not truncated",
])
def test_predicates_parse_into_an_ast(text):
    pred = prog.parse_predicate(text, "t")
    assert pred.text == text and pred.comparisons()


@pytest.mark.parametrize("text, message", [
    ("", "non-empty"),
    ("rowz == 0", "unknown predicate name"),
    ("n.rowz == 0", "unknown predicate name"),
    ("rows", "needs a comparison"),
    ("rows ==", "ends after"),
    ("rows == 0 or truncated", "unexpected"),
    ("rows == true", "not comparable"),
    ("truncated == 1", "not comparable"),
    ("truncated > true", "only supports"),
    ("rows == MAYBE", "unknown constant"),
    ("rows == 0 and", "expected a predicate name"),
    ("rows == 0 ; import os", "cannot read"),
    ("__import__('os')", "unknown predicate name|cannot read"),
    ("Bad.rows == 0", "bad node qualifier"),
    ("view_status == 3", "not comparable"),
    ("rows == some_word", "bad value"),
    ("(rows == 0)", "cannot read"),
])
def test_bad_predicates_are_refused(text, message):
    with pytest.raises(prog.ProgramError, match=message):
        prog.parse_predicate(text, "t")


def test_every_vocabulary_name_is_in_the_spec_list():
    assert prog.PREDICATE_NAMES == {
        "rows", "rows_total", "truncated", "distinct_documents", "docs_cited", "max_relevance", "score_drop",
        "payload_chars", "persisted", "entity_docs", "rows_below_k", "twins", "stale", "view_status",
        "history_matches", "concentration", "level_agreement",
    }
    assert prog.CONSTANTS["NEEDLE_MARGIN"] == 0.03


def test_predicate_references_are_checked_in_the_program():
    base = {"id": "p", "par": [_run(1)]}
    _bad([base, {"id": "u", "pick": {"from": "p", "how": "union"}, "when": "ghost.rows > 0"}], "unknown node 'ghost'")
    _bad([base, {"id": "u", "pick": {"from": "p", "how": "union"}, "when": "rows > 0"}], "qualify")
    _bad([{"id": "u", "run": BRIEF_VECTOR, "when": "later.rows > 0"}, {"id": "later", "run": BRIEF_VECTOR}], "not run yet")
    _bad([base, {"id": "u", "pick": {"from": "p", "how": "best_of", "score": ["ghost.rows > 0"]}}], "unknown node")
    _bad([{"id": "u", "run": BRIEF_VECTOR, "when": "rows > banana"}], "unknown predicate|bad value|unknown constant")


@pytest.mark.parametrize("guard, message", [
    ({"on": "ghost", "if": "truncated", "then": "escalate"}, "unknown node"),
    ({"on": "r1", "if": "ghost.truncated", "then": "escalate"}, "unknown node"),
    ({"on": "r1", "if": "nope", "then": "escalate"}, "unknown predicate"),
    ({"on": "r1", "if": "truncated", "then": "shrug"}, "must be"),
    ({"on": "r1", "if": "truncated", "then": {"replace": "nowhere"}}, "unknown route"),
    ({"on": "r1", "if": "truncated", "then": {"replace": "p1"}}, "its own route"),
    ({"on": "r1", "if": "truncated", "then": {"retry_with": {}}}, "retry_with"),
    ({"on": "r1", "if": "truncated", "then": {"retry_with": {"Bad": "x"}}}, "retry_with"),
    ({"on": "r1", "if": "truncated", "then": {"page": {"from": "r1", "until": "rows_total"}}}, "drop `from`"),
    ({"on": "r1", "if": "truncated", "then": {"page": {"until": "k"}}}, "k must be"),
    ({"on": "r1", "if": "truncated", "then": {"run": BRIEF_VECTOR}}, "converted from a v1"),
    ({"on": "r1", "if": "truncated", "then": {"mystery": 1}}, "unknown action"),
    ({"on": "r1", "if": "truncated"}, "on, if, then"),
    ({"on": "r1", "if": "truncated", "then": "escalate", "extra": 1}, "on, if, then"),
])
def test_guard_violations_are_named(guard, message):
    with pytest.raises(routes.RouteError, match=message):
        routes.parse_routes(_program_route([_run(1)], guards=[guard]))


def test_guard_page_needs_a_run_node():
    program = [{"id": "q", "par": [_run(1)]}]
    guards = [{"on": "q", "if": "truncated", "then": {"page": {"until": "rows_total"}}}]
    with pytest.raises(routes.RouteError, match="run node"):
        routes.parse_routes(_program_route(program, guards=guards))


def test_every_guard_action_parses():
    data = _program_route([_run(1)], guards=[
        {"on": "r1", "if": "rows == 0", "then": {"replace": "other"}},
        {"on": "r1", "if": "twins > 1", "then": {"retry_with": {"categories": "wide", "n": 3}}},
        {"on": "r1", "if": "rows == 0 and not truncated", "then": "widen_domains"},
        {"on": "r1", "if": "truncated", "then": {"page": {"until": "rows_total"}}},
        {"on": "r1", "if": "stale", "then": "escalate"},
    ])
    data["routes"].append({**_one(id="other")["routes"][0]})
    route = routes.parse_routes(data)[0]
    assert [g.then.kind for g in route.guards] == ["replace", "retry_with", "widen_domains", "page", "escalate"]
    assert route.guards[0].then.route == "other"
    assert route.guards[1].then.slots == (("categories", "wide"), ("n", "3"))


def test_a_v1_route_reads_as_a_program_with_guards():
    steps = [BRIEF_VECTOR, "artmind query insights list --domain {domains} --compact"]
    fallbacks = [{"when": w, "cmd": BRIEF_VECTOR} for w in sorted(routes.FALLBACK_WHEN)]
    (route,) = routes.parse_routes(_one(steps=steps, fallbacks=fallbacks))
    assert not route.authored_program
    assert [(n.id, n.kind, n.run) for n in route.program] == [("s1", "run", steps[0]), ("s2", "run", steps[1])]
    assert route.steps == tuple(steps)
    by_when = {g.v1_when: g for g in route.guards}
    assert set(by_when) == set(routes.FALLBACK_WHEN)
    assert all(g.on == "s1" and g.then.kind == "run" and g.then.run == BRIEF_VECTOR for g in route.guards)
    assert by_when["zero_rows"].when.text == "rows == 0"
    assert by_when["truncated"].when.text == "truncated"
    assert {w: g.when.text for w, g in by_when.items()} == prog.V1_FALLBACK_PREDICATES


def test_every_packaged_route_has_a_program_and_guards_for_its_fallbacks():
    for route in routes.load_routes():
        assert route.program, route.id
        assert [n.run for n in prog.run_nodes(route.program)] == list(route.steps)
        if route.chooses:  # authored guards are not v1 fallbacks
            continue
        assert [(g.v1_when, g.then.run) for g in route.guards] == [(f.when, f.cmd) for f in route.fallbacks]


def _all_run_templates(route):
    templates = [n.run for choice in route.programs for n in prog.run_nodes(choice.program)]
    templates += [g.then.run for choice in route.programs for g in choice.guards if g.then.kind == "run"]
    templates += [t for v in route.variants.values() for t in v]
    templates += list(route.optional_steps)
    templates += [f.cmd for f in route.fallbacks]
    return templates


def _click_path(template: str) -> str:
    """The Click command path a template invokes, spelled as WRITE_COMMANDS does (e.g. 'ingest sync')."""
    from artmind.cli import cli

    tokens = shlex.split(routes.render(template, {name: _SENTINEL for name in (*routes.ALL_PLACEHOLDERS, "phrase", "twin", "doc")}))
    command, parts = cli, []
    for token in tokens[1:]:
        if hasattr(command, "commands") and token in command.commands:
            command = command.commands[token]
            parts.append(token)
        else:
            break
    return " ".join(parts)


def test_every_packaged_template_passes_compact_brief_and_is_read_only():
    from artmind import cli_conventions

    for route in routes.load_routes():
        for template in _all_run_templates(route):
            tokens = shlex.split(template)
            assert "--compact" in tokens, template
            if any(known in template for known in RETRIEVAL_COMMANDS):
                assert "--shape" in tokens and tokens[tokens.index("--shape") + 1] == "brief", template
            assert _click_path(template) not in cli_conventions.WRITE_COMMANDS, (route.id, template)


def test_write_commands_are_recognised_by_path():
    from artmind import cli_conventions

    assert _click_path("artmind ingest sync --compact") in cli_conventions.WRITE_COMMANDS
    assert _click_path("artmind query graph pattern7 --domain {domains} --compact") not in cli_conventions.WRITE_COMMANDS


def test_the_survey_example_templates_are_real_commands():
    for template in (NEEDLE, WIDE):
        command, rest = _resolve(template)
        by_name, options, _ = _parse_args(command, rest)
        assert set(options) <= set(by_name), template


# ── routes v2: several programs per route (planner v2 P2b) ───────────────────

ONE = "artmind query vector-text --shape brief --domain {domains} {search_term} --compact"
PHRASE = "artmind query entity-resolve --domain {domains} --topK 25 {phrase} --compact"


def _choice(kind="one_step", when=None, program=None, guards=None):
    entry = {"kind": kind, "program": program or [_run(1, ONE)]}
    if when is not None:
        entry["when"] = when
    if guards is not None:
        entry["guards"] = guards
    return entry


def _programs_route(*choices):
    return _v2(programs=list(choices))


def test_programs_parse_with_a_default_last_entry():
    (route,) = routes.parse_routes(_programs_route(
        _choice("existence_concept", "negative and rare_term",
                [{"id": "m", "map": {"over": "phrasings", "as": "phrase", "max": 4, "do": _run(1, PHRASE)}}]),
        _choice("kinship_aggregate", "twins > 0"),
        _choice("one_step")))
    assert route.chooses and route.authored_program
    assert [c.kind for c in route.programs] == ["existence_concept", "kinship_aggregate", "one_step"]
    assert [c.when.text if c.when else None for c in route.programs] == ["negative and rare_term", "twins > 0", None]
    # Route.program / guards / kind / steps are the default (last) choice
    assert route.kind == "one_step" and route.program == route.programs[-1].program
    assert route.steps == (ONE,) and route.guards == ()


def test_single_program_kind_defaults_to_one_step_and_v1_is_sequence():
    (route,) = routes.parse_routes(_program_route())
    assert route.kind == "one_step" and not route.chooses and len(route.programs) == 1
    assert route.programs[0].when is None
    data = _v2(program=SURVEY_PROGRAM, kind="chunks_cross_document", guards=[])
    (route,) = routes.parse_routes(data)
    assert route.kind == "chunks_cross_document"
    (v1,) = routes.parse_routes({"version": 2, "routes": [{**_v2()["routes"][0], "steps": [BRIEF_VECTOR]}]})
    assert v1.kind == "sequence" and not v1.chooses and v1.programs[0].kind == "sequence"


def test_every_packaged_route_has_one_choice_and_does_not_choose():
    choosing = {"existence": 3, "enumeration": 2, "aggregate": 2, "entity_profile": 2, "document": 2}
    for route in routes.load_routes():
        if route.id in choosing:
            assert route.chooses and len(route.programs) == choosing[route.id], route.id
            assert route.programs[-1].when is None
        else:
            assert len(route.programs) == 1 and not route.chooses, route.id


def test_choice_guards_are_parsed_against_their_own_program():
    (route,) = routes.parse_routes(_programs_route(
        _choice("one_step", "negative", guards=[{"on": "r1", "if": "rows == 0", "then": "escalate"}]),
        _choice("one_step", guards=[{"on": "r1", "if": "truncated", "then": {"page": {"until": "rows_total"}}}])))
    assert [g.then.kind for g in route.programs[0].guards] == ["escalate"]
    assert route.guards == route.programs[1].guards and route.guards[0].then.kind == "page"


def _map_program(over):
    return [{"id": "m", "map": {"over": over, "as": "phrase", "do": _run(1, PHRASE)}}]


@pytest.mark.parametrize("data, message", [
    (_programs_route(_choice(when="negative"), _choice(when="date_window")), None),  # a `when` on the last is allowed
    (_programs_route(_choice()), None),
    (_programs_route(_choice(), _choice(when="negative")), "only the last entry may omit"),
    (_programs_route(_choice(when="rows > 0"), _choice()), "unknown predicate name"),  # a step predicate
    (_programs_route(_choice(when="nope"), _choice()), "unknown predicate name"),
    (_programs_route(_choice(when="needle.twins > 0"), _choice()), "cannot name a node"),
    (_programs_route(_choice(when="twins"), _choice()), "needs a comparison"),
    (_programs_route(_choice(when="negative >= true"), _choice()), "only supports"),
    (_programs_route(_choice(when="twins == true"), _choice()), "not comparable"),
    (_programs_route(_choice(kind="nonsense", when="negative"), _choice()), "kind"),
    (_programs_route(_choice(kind="sequence", when="negative"), _choice()), "kind"),
    (_programs_route({"program": [_run(1, ONE)], "when": "negative"}, _choice()), "must be"),
    (_programs_route({"kind": "one_step", "program": [_run(1, ONE)], "extra": 1}), "must be"),
    (_programs_route(), "non-empty"),
    (_v2(programs=[_choice()], program=[_run(1, ONE)]), "exactly one"),
    (_v2(programs=[_choice()], steps=[BRIEF_VECTOR]), "exactly one"),
    ({**_programs_route(_choice()), "version": 1}, "version: 2"),
    (_v2(programs=[_choice()], guards=[]), "inside each entry"),
    (_v2(programs=[_choice()], kind="one_step"), "`kind:` only goes with a single"),
    (_v2(program=[_run(1, ONE)], kind="sequence"), "kind"),
    (_v2(program=[_run(1, ONE)], kind="nope"), "kind"),
    (_programs_route(_choice(program=_map_program("twins"))), "list slot"),
])
def test_programs_rule_violations_are_named(data, message):
    if message is None:
        routes.parse_routes(data)
        return
    with pytest.raises(routes.RouteError, match=message):
        routes.parse_routes(data)


def test_every_list_slot_is_a_valid_map_over():
    for over in prog.LIST_SLOTS:
        (route,) = routes.parse_routes(_programs_route(_choice(program=_map_program(over))))
        assert route.program[0].map.over == over


def test_the_vocabularies_are_the_documented_ones():
    assert prog.PLAN_FACT_NAMES == {"twins", "entities", "negative", "latest", "rare_term", "category",
                                    "date_window", "document", "chunks_only", "view_count", "occurrence"}
    assert set(prog.PROGRAM_KINDS) == {
        "one_step", "view_count", "kinship_aggregate", "enumeration", "entity_visits", "chunks_cross_document",
        "latest", "history", "absence", "existence_concept", "draw_connected", "batch_claims", "sequence"}
    assert prog.LIST_SLOTS == ("twin_ids", "phrasings", "years", "family_documents")


@pytest.mark.parametrize("text, facts, expected", [
    ("twins > 0", {"twins": 2}, True), ("twins > 0", {"twins": 0}, False), ("twins > 0", {}, False),
    ("negative and rare_term", {"negative": True, "rare_term": True}, True),
    ("negative and rare_term", {"negative": True, "rare_term": False}, False),
    ("not date_window", {}, True), ("not date_window", {"date_window": True}, False),
    ("entities >= 2 and not chunks_only", {"entities": 2}, True),
    ("latest == false", {"latest": False}, True),
])
def test_plan_predicates_evaluate_over_facts(text, facts, expected):
    assert prog.evaluate_plan_predicate(prog.parse_when(text, "t"), facts) is expected
