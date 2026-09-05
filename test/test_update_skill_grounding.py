"""Guards artmind-update/SKILL.md's input-fidelity grounding rule against drift.

Found live: the chat agent, following this skill, took a terse user input
("I want too add that there was a 1999 Internet stocks meltdown as well as an
example of Markets down") and passed `--text` an elaborated paragraph full of
facts the user never stated (NASDAQ, "dot-com bubble", venture capital
scrutiny, ...). `update.py` stores `--text` as `raw_text` on the graph's
UserChat node verbatim (no server-side rewriting -- confirmed by reading
update.py:626) and extraction runs over it, so the embellishment was entirely
the agent's own initiative, not a code bug: nothing in the skill's prior text
told it not to. Mirrors test_query_skill_structural_schema.py's precedent --
plain-text read of the skill file, no Neo4j, no network.
"""

from pathlib import Path

SKILL_PATH = Path(__file__).resolve().parent.parent / "artmind" / "skills" / "artmind-update" / "SKILL.md"


def _skill_text() -> str:
    return SKILL_PATH.read_text()


def test_skill_file_exists() -> None:
    assert SKILL_PATH.is_file(), f"expected {SKILL_PATH} to exist"


def test_grounding_rules_forbid_embellishing_the_input_text() -> None:
    text = _skill_text()
    assert "## Grounding Rules" in text
    grounding_section = text.split("## Grounding Rules", 1)[1].split("## Required Inputs", 1)[0]
    assert "verbatim" in grounding_section, (
        "artmind-update/SKILL.md's Grounding Rules must tell the agent to pass "
        "--text verbatim -- raw_text is permanent graph provenance, and "
        "extraction runs over it, so an agent 'helpfully' elaborating the "
        "user's input plants facts in the graph the user never stated."
    )


def test_required_inputs_also_points_back_at_the_verbatim_rule() -> None:
    """A reader skimming straight to `text`'s own bullet (not the rules list
    above it) should still be pointed at the same constraint."""
    text = _skill_text()
    assert "## Required Inputs" in text
    required_section = text.split("## Required Inputs", 1)[1].split("## Session Setup", 1)[0]
    assert "verbatim" in required_section
