"""routes.yaml and its loader (spec 2026-10-22 §4.3)."""

import shlex
from pathlib import Path

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


def _command_and_options(template: str):
    from artmind.cli import cli

    filled = routes.render(template, {name: "x" for name in routes.PLACEHOLDERS})
    tokens = shlex.split(filled)
    assert tokens[0] == "artmind", template
    command, i = cli, 1
    while i < len(tokens) and hasattr(command, "commands") and tokens[i] in command.commands:
        command = command.commands[tokens[i]]
        i += 1
    known = {opt for param in command.params for opt in getattr(param, "opts", [])}
    used = [t for t in tokens[i:] if t.startswith("--")]
    return command, used, known


@pytest.mark.parametrize("route", routes.load_routes(), ids=lambda r: r.id)
def test_every_template_names_a_real_command_and_real_options(route):
    for template in [*route.steps, *(f.cmd for f in route.fallbacks)]:
        command, used, known = _command_and_options(template)
        assert not hasattr(command, "commands") or getattr(command, "invoke_without_command", False), template
        assert set(used) <= known, f"{template}: unknown {sorted(set(used) - known)}"


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
    (_one(evidence=[]), "must not be empty"),
    (_one(evidence=["Q7"]), "evidence"),
    (_one(fallbacks=[{"when": "later", "cmd": "artmind query chunks --compact"}]), "fallbacks"),
    (_one(needs=["magic"]), "unknown needs"),
    (_one(triggers=["on {when}"]), "unknown slot"),
    (_one(description="x" * 301), "description"),
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
