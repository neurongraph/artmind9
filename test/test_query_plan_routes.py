"""routes.yaml and its loader (spec 2026-10-22 §4.3)."""

import shlex
from pathlib import Path

import click
import pytest

from artmind.query_plan import routes

EXPECTED_IDS = {
    "existence", "enumeration", "aggregate", "superlative", "entity_profile", "entity_history",
    "time_history", "conflicts", "hierarchy", "path", "document", "inventory", "procedure",
    "structured", "insights", "filing", "search",
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

    filled = routes.render(template, {name: _SENTINEL for name in routes.PLACEHOLDERS})
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
    for template in [*route.steps, *(t for v in route.variants.values() for t in v), *(f.cmd for f in route.fallbacks)]:
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
        templates = [*route.steps, *(t for v in route.variants.values() for t in v), *(f.cmd for f in route.fallbacks)]
        for template in templates:
            used |= routes.placeholders(template)
    assert used <= supplied, sorted(used - supplied)
    assert routes.PLACEHOLDERS <= supplied, sorted(routes.PLACEHOLDERS - supplied)
