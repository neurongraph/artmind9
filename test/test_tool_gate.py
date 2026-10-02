"""The Bash gate for grounded agent surfaces (docs/vault.md).

The threat model is a helpful model taking a shortcut -- not an adversary
evading a sandbox. So a prefix allowlist plus a metacharacter ban is the right
strength: it removes the easy path and signposts the right one.
"""
from __future__ import annotations

import os

import pytest

from artmind.webui.tool_gate import DENIED_TOOLS, is_allowed_bash, is_allowed_skill_read


@pytest.mark.parametrize("command", [
    "artmind query vector-text --domain banking 'what is the rate?'",
    "artmind query graph pattern1 --domain fiction --entityClass PERSON",
    "artmind query domains-overview --compact",
    "  artmind query chunks --idList abc123",
])
def test_artmind_commands_are_allowed(command):
    assert is_allowed_bash(command) is True


@pytest.mark.parametrize("command", [
    "cat policies/policy_aml.md",
    "grep -r 'compensation' .",
    "rg compensation",
    "ls -R",
    "head -50 notes/journal.md",
    "find . -name '*.md'",
    "python -c \"print(open('x.md').read())\"",
])
def test_reading_the_vault_directly_is_denied(command):
    assert is_allowed_bash(command) is False


@pytest.mark.parametrize("command", [
    "artmind query domains-overview; cat policies/policy_aml.md",
    "artmind query domains-overview && grep -r x .",
    "artmind query domains-overview | tee /tmp/out",
    "artmind query domains-overview > /tmp/out",
    "artmind $(cat /etc/passwd)",
    "artmind query `whoami`",
    "artmind query x\ncat secrets.md",
    "artmind query x & cat secrets.md",
])
def test_chaining_past_the_prefix_is_denied(command):
    """A prefix match alone would pass every one of these."""
    assert is_allowed_bash(command) is False


def test_a_command_merely_starting_with_the_word_artmind_is_not_enough():
    """`artmindfoo` and `artmind-something` are not `artmind`."""
    assert is_allowed_bash("artmindfoo --help") is False
    assert is_allowed_bash("artmind-query x") is False


def test_an_empty_command_is_denied():
    assert is_allowed_bash("") is False
    assert is_allowed_bash("   ") is False


def test_the_denied_tool_list_covers_every_way_to_read_a_file():
    """If the SDK gains another file-reading tool, this list must grow -- the
    gate is only as good as its coverage."""
    assert {"Read", "Grep", "Glob"} <= set(DENIED_TOOLS)


def test_the_qa_surface_has_no_filesystem_access():
    """End-user Q&A: reading files is pure downside."""
    from artmind.webui.profiles import QA_PROFILE

    assert QA_PROFILE.filesystem_access is False


def test_the_benchmark_surface_has_no_filesystem_access():
    """A benchmark that greps its way to an answer silently invalidates every
    score in benchmarking/."""
    from artmind.webui.profiles import BENCHMARK_PROFILE

    assert BENCHMARK_PROFILE.filesystem_access is False


def test_the_admin_surface_keeps_filesystem_access():
    """An operator console must be able to inspect a failed conversion, read a
    log, and see what actually landed on disk."""
    from artmind.webui.profiles import ADMIN_PROFILE

    assert ADMIN_PROFILE.filesystem_access is True


@pytest.fixture
def skill_roots(tmp_path):
    """Two allowed roots, mirroring PACKAGE_SKILLS_DIR and <cwd>/.claude/skills."""
    package_skills = tmp_path / "package" / "artmind" / "skills"
    vault_skills = tmp_path / "vault" / ".claude" / "skills"
    (package_skills / "artmind-query").mkdir(parents=True)
    (vault_skills / "artmind-query").mkdir(parents=True)
    (package_skills / "artmind-query" / "SKILL.md").write_text("# query")
    return package_skills, vault_skills


def test_a_file_inside_the_package_skills_root_is_allowed(skill_roots):
    package_skills, vault_skills = skill_roots
    target = package_skills / "artmind-query" / "SKILL.md"

    assert is_allowed_skill_read(str(target), [package_skills, vault_skills]) is True


def test_a_file_inside_the_vault_skills_root_is_allowed(skill_roots):
    package_skills, vault_skills = skill_roots
    target = vault_skills / "artmind-query" / "SKILL.md"
    target.write_text("# query")

    assert is_allowed_skill_read(str(target), [package_skills, vault_skills]) is True


def test_a_dot_dot_escape_from_a_root_is_denied(skill_roots):
    package_skills, vault_skills = skill_roots
    secret = package_skills.parent.parent / "secrets.md"
    secret.write_text("shh")
    escaping = package_skills / "artmind-query" / ".." / ".." / ".." / "secrets.md"

    assert is_allowed_skill_read(str(escaping), [package_skills, vault_skills]) is False


def test_a_symlink_pointing_outside_a_root_is_denied(skill_roots, tmp_path):
    package_skills, vault_skills = skill_roots
    outside = tmp_path / "outside.md"
    outside.write_text("not a skill")
    link = package_skills / "artmind-query" / "escape.md"
    os.symlink(outside, link)

    assert is_allowed_skill_read(str(link), [package_skills, vault_skills]) is False


def test_a_sibling_dir_sharing_a_name_prefix_is_denied(skill_roots, tmp_path):
    package_skills, vault_skills = skill_roots
    evil = package_skills.parent / "skills-evil" / "SKILL.md"
    evil.parent.mkdir(parents=True)
    evil.write_text("not a skill")

    assert is_allowed_skill_read(str(evil), [package_skills, vault_skills]) is False


def test_a_non_skill_vault_file_is_denied(skill_roots, tmp_path):
    package_skills, vault_skills = skill_roots
    vault_root = vault_skills.parent.parent
    note = vault_root / "notes" / "journal.md"
    note.parent.mkdir(parents=True)
    note.write_text("private")

    assert is_allowed_skill_read(str(note), [package_skills, vault_skills]) is False
