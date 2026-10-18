from pathlib import Path

SKILLS = Path(__file__).resolve().parent.parent / "artmind" / "skills"


def flat(text):
    return " ".join(text.split())


def test_query_skill_insights_section():
    skill = (SKILLS / "artmind-query" / "SKILL.md").read_text()
    assert skill.index("## Insights") < skill.index("## The Query Protocol")
    section = flat(skill.split("## Insights", 1)[1].split("## The Query Protocol", 1)[0])
    for needle in ("artmind query insights list --domain", "artmind query insights show",
                   "query entity-context", "Co-occurrence on the same day, not cause", "as_of",
                   "never run `artmind insights refresh`"):
        assert needle in section, needle


def test_insights_operator_skill():
    text = (SKILLS / "artmind-insights" / "SKILL.md").read_text()
    assert text.startswith("---\nname: artmind-insights\ndescription: ")
    body = flat(text)
    for needle in ("artmind insights refresh", "artmind insights profile", "artmind insights config check",
                   "domains/insights/<domain>.yaml", "artmind insights same-as --export",
                   "artmind-create-schema", "artmind-curate", "artmind-create-view", "source_question",
                   "--no-narrate"):
        assert needle in body, needle
