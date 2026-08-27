"""Guards artmind-query/SKILL.md's structural-schema prose against drift.

Mirrors test_cli_guide.py's COMMAND_GROUPS precedent: the canonical facts
about the fixed structural graph (node labels, relationship types, history
label pairs) live as data in artmind/structural_schema.py, and this test
reads the skill file as plain text and checks it against that data — no
Neo4j, no network, per the hermetic suite's own rules.

This is the guard the Phase 7 prompt asked for. Without it, a future
structural change (a new relationship, a retired label) can update the code
and text2cypher's generated prompt while leaving the skill's own prose
teaching an agent the old shape — exactly what happened to the dead
`(:UserChat)-[:MENTIONS]->(:Entity)` line this phase removed.
"""

from pathlib import Path

from artmind.structural_schema import RETIRED_NAMES, canonical_names

SKILL_PATH = Path(__file__).resolve().parent.parent / "artmind" / "skills" / "artmind-query" / "SKILL.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text()


def test_skill_file_exists() -> None:
    assert SKILL_PATH.is_file(), f"expected {SKILL_PATH} to exist"


def test_every_canonical_name_is_named_in_the_skill() -> None:
    """Every label/relationship type the graph actually has must be named.

    Not "named in the structural-schema section specifically" — a name is
    free to appear anywhere in the file (e.g. a retrieve-table row) — the
    point is that an agent reading the whole skill would encounter it
    somewhere, not that it vanished after a rename.
    """
    text = _skill_text()
    missing = [name for name in canonical_names() if name not in text]
    assert not missing, (
        "These structural names exist in the graph (artmind/structural_schema.py) "
        "but are never mentioned in artmind-query/SKILL.md:\n  " + "\n  ".join(missing)
    )


def test_no_retired_name_survives_in_the_skill() -> None:
    """Catches a stale instruction — worse than a missing one (CLAUDE.md trap 3)."""
    text = _skill_text()
    present = [name for name in RETIRED_NAMES if name in text]
    assert not present, (
        "These names belong to a pre-redesign model and must not appear in "
        "artmind-query/SKILL.md (a stale instruction here is followed "
        "confidently by an agent):\n  " + "\n  ".join(present)
    )


def test_no_asof_default_instruction_survives() -> None:
    """The --asOf-by-default instruction inverted (Phase 4): it must not return.

    Checked separately from RETIRED_NAMES because the danger here isn't a
    dead token, it's a specific *sentence* whose meaning flipped — the
    projection is current by construction, so entity commands take no
    --asOf at all, and a surviving "default to --asOf" instruction would be
    actively wrong rather than merely absent.
    """
    text = _skill_text().lower()
    assert "default to `--asof" not in text
    assert "default to --asof" not in text
    assert "asof_ignored" not in text
