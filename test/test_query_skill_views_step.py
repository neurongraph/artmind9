"""artmind-query's protocol has a Views step, and every cross-reference is by name.

Rewritten for the orient text promoted 2026-11-11; the classic checks are in git history.

Spec 2026-10-09 section 8.1. Numbered cross-references ("skip to step 3") rot the
moment a step is inserted -- exactly what adding Views would do -- so the skill
refers to steps by name and this test keeps it that way.
"""

import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent / "artmind" / "skills" / "artmind-query"
SKILL = (SKILL_DIR / "SKILL.md").read_text()


def test_protocol_line_and_headings_in_order():
    assert "## The Query Protocol: Orient → Views → Resolve → Retrieve → Ground → Adjudicate" in SKILL
    headings = re.findall(r"^### (\d+)\. (\w+)", SKILL, re.M)
    assert headings == [
        ("1", "Views"), ("2", "Discover"), ("3", "Resolve"),
        ("4", "Retrieve"), ("5", "Ground"), ("6", "Adjudicate"),
    ]


def test_no_numeric_step_cross_references():
    offenders = []
    for path in [SKILL_DIR / "SKILL.md", *sorted((SKILL_DIR / "references").glob("*.md"))]:
        if path.name == "structured-store.md":
            continue  # its "steps 1-3" are that file's own local numbered list
        for n, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"\b[Ss]teps?\s+\d", line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, "refer to steps by name (Retrieve, not step 4):\n" + "\n".join(offenders)


def test_views_step_content():
    section = SKILL.split("### 1. Views", 1)[1].split("### 2. Discover", 1)[0]
    section = " ".join(section.split())  # ignore line wrapping
    for needle in (
        "artmind query views show",
        "artmind query views run",
        "--render markdown",
        "@<_id>",
        "needs_disambiguation",
        "no_match",
        "continue with Discover",
        "suggestions",
        "did you mean",
        "#### Store routing",
    ):
        assert needle in section, needle


def test_views_come_from_orient_not_a_listing_call():
    """Since the orient text was promoted (2026-11-11), peel 2 lists a domain's saved views, so the skill
    matches them from orient and never opens with `views list` or `db bridge` (spec 2026-11-09 §6.1)."""
    assert "artmind query views list --domain" not in SKILL and "artmind db bridge" not in SKILL
    orient = SKILL.split("## Orient — read the corpus first", 1)[1].split("\n## ", 1)[0]
    assert "views show" in orient and "views run" in orient
    assert SKILL.index("## Orient — read the corpus first") < SKILL.index("## Insights") < SKILL.index("## The Query Protocol")
    assert "a saved view still comes first" in SKILL.split("## Insights", 1)[1].split("## The Query Protocol", 1)[0]


def test_retrieve_table_routes_view_questions_to_views_run():
    retrieve = SKILL.split("### 4. Retrieve", 1)[1].split("### 5. Ground", 1)[0]
    table = retrieve.split("| Question shape | Command |", 1)[1]
    first_row = table.split("\n")[2]
    assert "views run" in first_row
