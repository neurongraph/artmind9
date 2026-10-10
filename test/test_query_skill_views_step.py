"""artmind-query's protocol has a Views step, and every cross-reference is by name.

Spec 2026-10-09 section 8.1. Numbered cross-references ("skip to step 3") rot the
moment a step is inserted -- exactly what adding Views would do -- so the skill
refers to steps by name and this test keeps it that way.
"""

import re
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent / "artmind" / "skills" / "artmind-query"
SKILL = (SKILL_DIR / "SKILL.md").read_text()


def test_protocol_line_and_headings_in_order():
    assert "## The Query Protocol: Route → Views → Discover → Resolve → Retrieve → Ground → Adjudicate" in SKILL
    headings = re.findall(r"^### (\d+)\. (\w+)", SKILL, re.M)
    assert headings == [
        ("0", "Route"), ("1", "Views"), ("2", "Discover"), ("3", "Resolve"),
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
        "once per domain per conversation",
        "artmind query views list --domain",
        "artmind query views show",
        "artmind query views run",
        "--render markdown",
        "@<_id>",
        "needs_disambiguation",
        "no_match",
        "continue with Discover",
        "suggestions",
        "did you mean",
    ):
        assert needle in section, needle


def test_route_hands_off_to_views_not_discover():
    route = SKILL.split("### 0. Route", 1)[1].split("### 1. Views", 1)[0]
    assert "skip to Discover" not in route and "go on to Views" in route
