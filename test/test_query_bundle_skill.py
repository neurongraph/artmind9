"""The bundle skill checked against data (P3 spec §8.2): commands exist, the shape is bounded, rules present."""

import re
import shlex
from pathlib import Path

import click

SKILL = Path(__file__).resolve().parents[1] / "artmind" / "skills" / "artmind-query-bundle" / "SKILL.md"


def _text():
    return SKILL.read_text(encoding="utf-8")


def test_frontmatter():
    head = _text().split("---")[1]
    assert re.search(r"^name: artmind-query-bundle$", head, re.M)
    assert re.search(r"^description: .{40,}", head, re.M)


def test_about_150_lines():
    assert 100 <= len(_text().splitlines()) <= 200


def test_every_command_named_exists_in_the_click_tree():
    from artmind.cli import cli

    for cmd in sorted(set(re.findall(r"`(artmind [a-z][^`]*)`", _text()))):
        tokens = shlex.split(cmd.replace("<", "").replace(">", ""))
        node, i = cli, 1
        while i < len(tokens) and hasattr(node, "commands") and tokens[i] in node.commands:
            node = node.commands[tokens[i]]
            i += 1
        assert i > 2, cmd  # at least `artmind <group> <command>`
        assert not isinstance(node, click.Group), cmd  # a leaf command, not a bare group
        opts = {o for p in node.params for o in getattr(p, "opts", [])}
        for t in tokens[i:]:
            if t.startswith("--"):
                assert t.split("=")[0] in opts, (cmd, t)


def test_the_one_call_and_the_rules():
    text = _text()
    assert '`artmind query run "<question>" --compact`' in text
    assert "query plan" in text and "never" in text.lower()  # never call query plan (§13 Q9)
    for word in ("evidence", "documents", "facts", "guides", "escalate", "handle", "coverage", "mode"):
        assert word in text, word
    assert "references/routes.md" in text and "references/commands.md" in text


def test_the_read_before_you_answer_rule():
    text = _text().lower()
    assert "read before you answer" in text
    assert "--expand" in text and "artmind query chunks" in text
    assert "never enough to state a fact" in text


def test_routes_named_exist():
    from artmind.query_plan import routes

    ids = {r.id for r in routes.load_routes()}
    for rid in set(re.findall(r"`route: ([a-z_]+)`", _text())):
        assert rid in ids, rid
