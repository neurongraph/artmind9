"""The orient skill variant (spec 2026-11-09 §6.1): orientation text replaced, commands real."""
import re
from pathlib import Path

from artmind.cli import cli

SKILL = Path(__file__).resolve().parent.parent / "artmind" / "skills" / "artmind-query-orient" / "SKILL.md"


def _text():
    return SKILL.read_text(encoding="utf-8")


def test_frontmatter_names_the_variant_and_orient_first():
    head = _text().split("---")[1]
    assert re.search(r"^name: artmind-query-orient$", head, re.M)
    assert "orient" in head and "views list" not in head


def test_orientation_commands_are_gone():
    text = _text()
    for gone in ("artmind query domains-overview", "artmind query views list", "artmind db bridge",
                 "artmind query graph metadata --domain <domain>",
                 "artmind query graph entity-listing --domain <domain> --countAll", "routing report",
                 "## The first two commands, every time", "### 0. Route", "Route's sub-agent",
                 "from Route", "When Route selected"):
        assert gone not in text, gone


def test_orient_section_has_the_six_rules():
    text = _text()
    section = text.split("## Orient — read the corpus first", 1)[1].split("\n## ", 1)[0]
    assert "artmind query orient --compact" in section
    assert "artmind query orient --domain" in section
    assert "`next` and `cuts[].rest`" in section
    assert len([ln for ln in section.splitlines() if re.match(r"^\d\. ", ln)]) == 6


def test_every_artmind_command_in_the_skill_exists():
    import click

    def resolve(tokens):
        cmd = cli
        for t in tokens:
            if not isinstance(cmd, click.Group) or t not in cmd.commands:
                return False
            cmd = cmd.commands[t]
        return True

    for match in re.finditer(r"artmind ((?:query|db|views|insights|topic)(?: [a-z0-9-]+){0,3})", _text()):
        words = match.group(1).split()
        assert any(resolve(words[:n]) for n in range(len(words), 0, -1)), match.group(0)
