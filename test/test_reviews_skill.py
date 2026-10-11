"""The artmind-review skill ships, is well-formed, and is in no web-UI profile."""

from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parents[1] / "artmind" / "skills" / "artmind-review"


def test_skill_frontmatter():
    text = (SKILL / "SKILL.md").read_text()
    assert text.startswith("---\n")
    meta = yaml.safe_load(text.split("---\n")[1])
    assert meta["name"] == "artmind-review"
    assert "dossier" in meta["description"]


def test_references_exist_and_are_linked():
    text = (SKILL / "SKILL.md").read_text()
    for ref in ("references/stage-walk.md", "references/dossier-format.md"):
        assert (SKILL / ref).is_file()
        assert ref in text


def test_skill_names_every_stage_and_the_privacy_rule():
    from artmind.reviews.dossier import STAGES

    walk = (SKILL / "references" / "stage-walk.md").read_text()
    for stage in STAGES:
        assert f"`{stage}`" in walk
    assert "leakcheck" in (SKILL / "SKILL.md").read_text()


def test_skill_is_in_no_webui_profile():
    from artmind.webui.profiles import ADMIN_PROFILE, BENCHMARK_PROFILE, QA_PROFILE

    for profile in (QA_PROFILE, ADMIN_PROFILE, BENCHMARK_PROFILE):
        assert "artmind-review" not in profile.skills


def test_skill_uses_the_shape_tools():
    walk = (SKILL / "references" / "stage-walk.md").read_text()
    skill = (SKILL / "SKILL.md").read_text()
    assert "artmind review shape" in walk and "artmind review reextract" in walk
    assert "later phase" not in skill
    assert "outside the checkout" in walk  # proposed schema scratch files stay out of the public repo
