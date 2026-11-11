"""Each query skill's references/commands.md matches the CLI (P2b, spec 2026-11-11 §5.4)."""
from pathlib import Path

import pytest

from artmind.cli_guide import render_markdown

SKILLS = Path(__file__).resolve().parent.parent / "artmind" / "skills"
TARGETS = sorted(SKILLS.glob("artmind-query*/references/commands.md"))


def test_some_skill_has_commands_md():
    assert TARGETS, "no artmind-query*/references/commands.md; run: just dev-skill-commands"


@pytest.mark.parametrize("path", TARGETS, ids=lambda p: p.parent.parent.name)
def test_commands_md_is_current(path):
    skill = path.parent.parent.name
    assert path.read_text(encoding="utf-8") == render_markdown(), (
        f"{skill}/references/commands.md is stale; run: just dev-skill-commands {skill}")
