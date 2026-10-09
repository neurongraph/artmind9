"""artmind-create-view ships with consistent frontmatter, references, and routing."""

import re
from pathlib import Path

from artmind.webui.profiles import ADMIN_PROFILE, QA_PROFILE

SKILL_DIR = Path(__file__).resolve().parent.parent / "artmind" / "skills" / "artmind-create-view"
OPENCODE = Path(__file__).resolve().parent.parent / "artmind" / "opencode" / "agent"


def test_frontmatter_name_matches_the_directory():
    text = (SKILL_DIR / "SKILL.md").read_text()
    assert re.match(r"---\nname: artmind-create-view\ndescription: .+\n---\n", text)


def test_references_named_in_the_skill_exist():
    text = (SKILL_DIR / "SKILL.md").read_text()
    for ref in set(re.findall(r"references/[\w/.]+\.md", text)):
        assert (SKILL_DIR / ref).is_file(), ref
    assert (SKILL_DIR / "references" / "view_format.md").is_file()


def test_every_cli_command_the_skill_names_exists():
    from artmind.cli import cli

    text = (SKILL_DIR / "SKILL.md").read_text()
    for group, sub in sorted(set(re.findall(r"artmind (views|query views) (\w+)", text))):
        node = cli
        for part in group.split():
            node = node.commands[part]
        assert sub in node.commands, f"artmind {group} {sub}"


def test_admin_only():
    assert "artmind-create-view" in ADMIN_PROFILE.skills
    assert "artmind-create-view" not in QA_PROFILE.skills


def test_opencode_personas_know_about_views():
    assert "artmind query views list" in (OPENCODE / "artmind.md").read_text()
    admin = (OPENCODE / "artmind-admin.md").read_text()
    assert "artmind-create-view" in admin and "artmind query views list" in admin
