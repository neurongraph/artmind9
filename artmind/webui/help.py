"""Generates the admin help/concept catalogue from sources of truth.

Never hand-write descriptions here — the wizard drifted because it did. Skill
concepts pull their text from SKILL.md front-matter (seeded into the run
folder's .claude/skills/ by `artmind init`); CLI concepts pull their text from
the command's own docstring (the same text `--help` shows). Editing either
source changes what this returns with no code change here.
"""
from artmind.ingest import _parse_md_frontmatter
from artmind.webui.profiles import ADMIN_PROFILE
from paths import ARTMIND_HOME

# Which concepts wipe or otherwise irreversibly modify graph data — this is
# structural metadata (reviewed alongside the code that makes it true), not
# descriptive text, so it's the one thing kept here rather than generated.
#
# artmind-curate is deliberately absent: same-as groups are declarative
# (same_as.yaml) and reversible by removing the group and rebuilding — there
# is no destructive graph surgery left in that skill's workflow, unlike its
# predecessor artmind-refine (whose merges deleted alias nodes with no
# un-merge).
_DESTRUCTIVE_CONCEPTS = {
    "artmind-update": True,   # retracts prior facts (no CLI un-retract)
    "snapshot": True,         # restore/import wipes Neo4j
}


def _skill_concepts() -> list[dict]:
    concepts = []
    for skill_name in ADMIN_PROFILE.skills:
        skill_md = ARTMIND_HOME / ".claude" / "skills" / skill_name / "SKILL.md"
        if not skill_md.exists():
            continue
        meta, _ = _parse_md_frontmatter(skill_md.read_text(encoding="utf-8"))
        description = meta.get("description")
        if not description:
            continue
        concepts.append({
            "key": skill_name,
            "title": skill_name.removeprefix("artmind-").replace("-", " "),
            "description": description,
            "source": "skill",
            "destructive": _DESTRUCTIVE_CONCEPTS.get(skill_name, False),
        })
    return concepts


def _cli_concepts() -> list[dict]:
    from artmind.cli import ingest_extract_kg, ingest_write_to_graph, session_initiate

    commands = {
        "chunk-resume": ("chunk resume", ingest_extract_kg),
        "kg-bundle": ("KG bundle", ingest_write_to_graph),
        "snapshot": ("snapshot", session_initiate),
    }
    concepts = []
    for key, (title, command) in commands.items():
        description = command.help
        if not description:
            continue
        concepts.append({
            "key": key,
            "title": title,
            "description": description,
            "source": "cli",
            "destructive": _DESTRUCTIVE_CONCEPTS.get(key, False),
        })
    return concepts


def get_concepts() -> list[dict]:
    """Return the full concept catalogue: maintenance skills + deterministic CLI ops."""
    return _skill_concepts() + _cli_concepts()
