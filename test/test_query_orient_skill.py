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


CLASSIC = SKILL.parent.parent / "artmind-query" / "SKILL.md"
LINES_BEFORE_P2_BATCH = 469

# Two distinctive phrases per deleted rung / section, taken from the classic skill.
_DELETED = {
    "ladder 2": ("pattern6 empty", "pattern5 `--mode shortest`."),
    "ladder 3": ("entity-context/pattern chunks too thin", "before falling back to search"),
    "ladder 6": ("compare relationship names", "use PART_OF to connect DocChunk to Document"),
    "ladder 7": ("text2cypher generates invalid Cypher", "generates invalid Cypher → vector-text"),
    "ladder 9": ("hand-written SQL", "compare the generated SQL against"),
    "ladder 10": ("widen `--topK`", "drop `--column` to resolve against the graph only"),
    "ladder 11": ("re-run discovery mode", "only be stored as a property"),
    "resolve": ("Pick the canonical entity", "Ids never fan out"),
    "counts": ("rows_total_capped", "walk the rest"),
    "hub": ("Drill in with", "Pass `--query \"<the question>\"`."),
}

_NEW_LINES = (
    "Empty or thin results carry `next`: run it when it serves the question.",
    "`entity-resolve` maps I/me to the narrator and groups same-class twins (`twins`): treat a twin group as one entity unless the text distinguishes them.",
    "Answer a count only from `complete: true`; otherwise say N of M, or rerun with `--all` (the `next`). For \"every year / each time\" questions use `survey --by year` (or `month`): it reports every period with a hit.",
    "Pass the question to `entity-context`; its `cut` block says what a hub's cut hides, and `next` names the command for the rest.",
    "Retired documents are not in search results. A result whose `doc.replaced` lists an older version, or a dated / 'what changed' question, goes to the graph: `document-history`, `pattern10 --includeHistory`, `entity-history`.",
    "`artmind query graph pattern8 --entityClass CLASS --entityId <id> --all`",
    "An event counts for a person only when the notes put them in it.",
)


def test_deleted_rules_are_absent_and_present_in_classic():
    variant, classic = _text(), CLASSIC.read_text(encoding="utf-8")
    for name, phrases in _DELETED.items():
        for ph in phrases:
            assert ph in classic, (name, ph)
            assert ph not in variant, (name, ph)


def test_new_command_absorbed_lines_present():
    text = _text()
    for line in _NEW_LINES:
        assert line in text, line


def test_variant_shrank():
    assert len(_text().splitlines()) < LINES_BEFORE_P2_BATCH


def test_named_options_exist():
    import click
    q = cli.commands["query"]
    def opts(cmd):
        return {o for p in cmd.params for o in getattr(p, "opts", [])}
    assert "--all" in opts(q.commands["graph"].commands["pattern8"])
    assert "--entityId" in opts(q.commands["graph"].commands["pattern8"])
    assert "--entityClass" in opts(q.commands["graph"].commands["pattern8"])
    assert {"--by", "--perPeriod"} <= opts(q.commands["survey"])
    assert "--documentName" in opts(q.commands["document-history"])
    assert any(p.name == "question" and isinstance(p, click.Argument)
               for p in q.commands["entity-context"].params)
