"""Tests for the generated admin help/concept catalogue (artmind/webui/help.py)."""

from artmind.webui import help as help_module


def _write_skill(base, name, description):
    skill_dir = base / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody text.\n"
    )


def test_skill_concept_description_is_read_verbatim(monkeypatch, tmp_path):
    monkeypatch.setattr(help_module, "ARTMIND_HOME", tmp_path)
    _write_skill(tmp_path, "artmind-query", "Ask about the graph.")
    concepts = help_module._skill_concepts()
    query_concept = next(c for c in concepts if c["key"] == "artmind-query")
    assert query_concept["description"] == "Ask about the graph."


def test_editing_skill_description_changes_output_with_no_code_change(monkeypatch, tmp_path):
    monkeypatch.setattr(help_module, "ARTMIND_HOME", tmp_path)
    _write_skill(tmp_path, "artmind-query", "Original description.")
    first = next(c for c in help_module._skill_concepts() if c["key"] == "artmind-query")
    assert first["description"] == "Original description."

    _write_skill(tmp_path, "artmind-query", "Updated description after an edit.")
    second = next(c for c in help_module._skill_concepts() if c["key"] == "artmind-query")
    assert second["description"] == "Updated description after an edit."


def test_skill_concepts_skip_missing_skill_dirs(monkeypatch, tmp_path):
    monkeypatch.setattr(help_module, "ARTMIND_HOME", tmp_path)
    concepts = help_module._skill_concepts()
    assert concepts == []


def test_skill_concepts_cover_admin_profile_skills(monkeypatch, tmp_path):
    monkeypatch.setattr(help_module, "ARTMIND_HOME", tmp_path)
    for skill_name in help_module.ADMIN_PROFILE.skills:
        _write_skill(tmp_path, skill_name, f"Description for {skill_name}.")
    concepts = help_module._skill_concepts()
    keys = {c["key"] for c in concepts}
    assert keys == set(help_module.ADMIN_PROFILE.skills)
    for c in concepts:
        assert c["source"] == "skill"


def test_update_flagged_destructive_curate_and_query_not(monkeypatch, tmp_path):
    # artmind-curate is deliberately NOT flagged: same-as groups are
    # declarative and reversible (remove the group, rebuild) — there is no
    # destructive graph surgery left in that skill's workflow, unlike its
    # predecessor artmind-refine (whose merges deleted alias nodes with no
    # un-merge, and which this concept key replaced).
    monkeypatch.setattr(help_module, "ARTMIND_HOME", tmp_path)
    _write_skill(tmp_path, "artmind-curate", "Curate the graph.")
    _write_skill(tmp_path, "artmind-update", "Update the graph.")
    _write_skill(tmp_path, "artmind-query", "Ask about the graph.")
    concepts = {c["key"]: c for c in help_module._skill_concepts()}
    assert concepts["artmind-update"]["destructive"] is True
    assert concepts["artmind-curate"]["destructive"] is False
    assert concepts["artmind-query"]["destructive"] is False


def test_cli_concepts_pull_real_command_docstrings():
    concepts = {c["key"]: c for c in help_module._cli_concepts()}
    assert set(concepts) == {"chunk-resume", "kg-bundle", "snapshot"}
    for c in concepts.values():
        assert c["source"] == "cli"
        assert isinstance(c["description"], str) and c["description"]
    assert concepts["snapshot"]["destructive"] is True
    assert concepts["chunk-resume"]["destructive"] is False


def test_get_concepts_combines_skill_and_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(help_module, "ARTMIND_HOME", tmp_path)
    for skill_name in help_module.ADMIN_PROFILE.skills:
        _write_skill(tmp_path, skill_name, f"Description for {skill_name}.")
    concepts = help_module.get_concepts()
    keys = {c["key"] for c in concepts}
    assert keys == set(help_module.ADMIN_PROFILE.skills) | {"chunk-resume", "kg-bundle", "snapshot"}
