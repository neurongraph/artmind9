"""artmind-query-p2b (spec 2026-11-11 skill progressive disclosure §3-§4, §6).

SOURCE is the text the variant was split from. Until promotion it is the live artmind-query skill; Task 11 points
it at the archived copy.
"""
import importlib.util
import re
from collections import Counter
from pathlib import Path

import click

from artmind.cli import cli

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("split_p2b_skill", ROOT / "scripts" / "split_p2b_skill.py")
_split = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_split)
REWRITES = _split.REWRITES


def _rewritten(text: str, name: str | None = None) -> str:
    """Source text as the split leaves it: only the named cross-reference rewrites applied.

    name=None: the live SKILL.md, whose text is spread over every variant file, so all rewrites apply."""
    for file, old, new, _count in REWRITES:
        if name is None or file == name:
            text = text.replace(old, new)
    return text


VARIANT = ROOT / "artmind" / "skills" / "artmind-query-p2b"
SOURCE = ROOT / "artmind" / "skills" / "artmind-query" / "SKILL.md"
SOURCE_REFS = SOURCE.parent / "references"
SKILL = (VARIANT / "SKILL.md").read_text(encoding="utf-8")
FILES = {p.name: p.read_text(encoding="utf-8") for p in [VARIANT / "SKILL.md", *sorted((VARIANT / "references").glob("*.md"))]
         if p.name != "commands.md"}

# Lines the live SKILL.md and an existing reference file (structured-store.md) already both carried, so the
# variant holds them in two files by design: the existing reference was kept whole, the SKILL.md copy moved.
ALREADY_DUPLICATED_IN_SOURCE = ("A domain can also have tabular data (csv/xlsx ingested via `artmind ingest`) li",)

VERBATIM = ("## Every turn, in this order", "## Orient — read the corpus first",
            "## Plan the parts — after orient, before any retrieval", "## Read every result before you answer")


def _significant(text: str) -> list[str]:
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if len(s) < 30 or s.startswith("#") or s.startswith("```") or re.fullmatch(r"\|[-| ]+\|", s):
            continue
        out.append(s)
    return out


def test_every_source_line_lands_in_exactly_one_file():
    source = _rewritten(SOURCE.read_text(encoding="utf-8"))
    once = [s for s, n in Counter(_significant(source)).items() if n == 1]
    lines_by_file = {name: set(_significant(text)) for name, text in FILES.items()}
    lost, doubled = [], []
    for s in once:
        if s.startswith(ALREADY_DUPLICATED_IN_SOURCE):
            continue
        homes = [name for name, lines in lines_by_file.items() if s in lines]
        if not homes:
            lost.append(s[:80])
        elif len(homes) > 1:
            doubled.append((s[:80], homes))
    assert not lost, "lines dropped in the split:\n" + "\n".join(lost)
    assert not doubled, f"lines in more than one file: {doubled}"


def test_existing_references_keep_their_text():
    for name in ("structured-store.md", "temporal.md", "conflicts.md", "hierarchy.md"):
        old = _rewritten((SOURCE_REFS / name).read_text(encoding="utf-8"), name).rstrip("\n")
        assert FILES[name].startswith(old), name


def _section(text: str, heading: str) -> str:
    return text.split(heading, 1)[1].split("\n## ", 1)[0]


def test_the_four_opening_sections_are_verbatim():
    source = _rewritten(SOURCE.read_text(encoding="utf-8"))
    for heading in VERBATIM:
        old, new = _section(source, heading).rstrip(), _section(SKILL, heading).rstrip()
        assert new.startswith(old), heading  # Read every result gains the guide lines at its end


def test_skill_names_itself_and_says_how_to_read_guide():
    assert re.search(r"^name: artmind-query-p2b$", SKILL.split("---")[1], re.M)
    reading = _section(SKILL, "## Read every result before you answer")
    assert "`guide`" in reading and "references/hubs.md" in reading


def test_every_artmind_command_named_exists():
    def resolve(tokens):
        cmd = cli
        for t in tokens:
            if not isinstance(cmd, click.Group) or t not in cmd.commands:
                return False
            cmd = cmd.commands[t]
        return True

    for name, text in FILES.items():
        for match in re.finditer(r"artmind ((?:query|db|views|insights|topic)(?: [a-z0-9-]+){0,3})", text):
            words = match.group(1).split()
            assert any(resolve(words[:n]) for n in range(len(words), 0, -1)), (name, match.group(0))


def test_size():
    assert len(SKILL.splitlines()) < 210  # spec §3: about 170, a target not a cap


def test_every_rewrite_is_applied_in_its_file():
    for file, old, new, _count in REWRITES:
        text = FILES[file]
        assert new in text, (file, new)
        assert old not in text, (file, old)
