"""artmind-query names only commands and options that exist.

The skill's prose is not pinned: wording is measured by benchmark runs, not by tests. What a test can
settle is a fact: every `artmind ...` command the skill (and its references) names is in the Click tree,
and the options it relies on are on those commands.
"""
import re
from pathlib import Path

import click

from artmind.cli import cli

SKILL_DIR = Path(__file__).resolve().parent.parent / "artmind" / "skills" / "artmind-query"


def _texts():
    return [p.read_text(encoding="utf-8") for p in [SKILL_DIR / "SKILL.md", *sorted((SKILL_DIR / "references").glob("*.md"))]]


def _resolve(tokens):
    cmd = cli
    for t in tokens:
        if not isinstance(cmd, click.Group) or t not in cmd.commands:
            return False
        cmd = cmd.commands[t]
    return True


def test_every_artmind_command_in_the_skill_exists():
    for text in _texts():
        for match in re.finditer(r"artmind ((?:query|db|views|insights|topic)(?: [a-z0-9-]+){0,3})", text):
            words = match.group(1).split()
            assert any(_resolve(words[:n]) for n in range(len(words), 0, -1)), match.group(0)


def test_named_options_exist():
    q = cli.commands["query"]

    def opts(cmd):
        return {o for p in cmd.params for o in getattr(p, "opts", [])}

    assert {"--all", "--entityId", "--entityClass"} <= opts(q.commands["graph"].commands["pattern8"])
    assert {"--by", "--perPeriod"} <= opts(q.commands["survey"])
    assert "--documentName" in opts(q.commands["document-history"])
    assert any(p.name == "question" and isinstance(p, click.Argument) for p in q.commands["entity-context"].params)
