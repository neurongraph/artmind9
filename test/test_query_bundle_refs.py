"""The bundle skill's generated references stay in step with routes.yaml and the Click tree (P3 spec §8.2)."""

from pathlib import Path

import pytest

from artmind.query_run import skill_refs

SKILLS = Path(skill_refs.__file__).resolve().parents[1] / "skills"
SKILL = SKILLS / "artmind-query-bundle"


def test_routes_md_is_current():
    assert (SKILL / "references" / "routes.md").read_text(encoding="utf-8") == skill_refs.routes_md(), \
        "stale: run `just dev-gen-query-refs`"


def test_commands_md_is_current():
    assert (SKILL / "references" / "commands.md").read_text(encoding="utf-8") == skill_refs.commands_md(), \
        "stale: run `just dev-gen-query-refs`"


def test_generation_is_deterministic():
    assert skill_refs.routes_md() == skill_refs.routes_md()
    assert skill_refs.commands_md() == skill_refs.commands_md()


def test_routes_md_names_every_route():
    from artmind.query_plan import routes

    text = skill_refs.routes_md()
    for route in routes.load_routes():
        assert f"## `{route.id}`" in text


def test_commands_md_lists_the_query_leaves_with_options():
    text = skill_refs.commands_md()
    for cmd in ("artmind query run", "artmind query page", "artmind query vector-text", "artmind query survey",
                "artmind query entity-context", "artmind query graph pattern7"):
        assert f"`{cmd}`" in text
    assert "--shape" in text and "--compact" in text


@pytest.mark.parametrize("name", skill_refs.SHARED)
def test_shared_reference_copy_matches_source(name):
    copy = SKILL / "references" / f"{name}.md"
    assert not copy.is_symlink()
    assert copy.read_bytes() == (SKILLS / "artmind-query" / "references" / f"{name}.md").read_bytes(), \
        "stale copy: run `just dev-gen-query-refs`"
