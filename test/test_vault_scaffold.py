"""What `artmind init` writes into a fresh directory (docs/vault.md)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from artmind import vault


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )
    return result.stdout


def _init_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    return tmp_path


def test_derived_data_is_ignored(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "data" / "kg").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "data" / "kg" / "doc.json").write_text("{}")

    assert "data/kg/doc.json" not in _git(tmp_path, "status", "--porcelain")


def test_curation_and_schemas_are_committed(tmp_path):
    """same_as.yaml is authoritative curation; losing it means redoing human
    merge adjudication."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind" / "domains" / "schemas").mkdir(parents=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "same_as.yaml").write_text("groups: []\n")
    (tmp_path / ".artmind" / "domains" / "schemas" / "general_schema.yaml").write_text("name: general\n")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")
    assert ".artmind/same_as.yaml" in status
    assert ".artmind/domains/schemas/general_schema.yaml" in status


def test_the_graph_password_is_never_committed(tmp_path):
    _init_repo(tmp_path)
    (tmp_path / ".artmind").mkdir(parents=True, exist_ok=True)
    vault.write_gitignore(tmp_path)
    (tmp_path / ".artmind" / "config.env").write_text("ARTMIND_KG_NEO4J_PASSWORD=secret\n")

    assert "config.env" not in _git(tmp_path, "status", "--porcelain", "--untracked-files=all")


def test_binaries_are_ignored_but_extracted_images_are_not(tmp_path):
    """The negation that matters: a .pptx is opaque and stays out, but the
    images docling extracted are referenced by committed markdown, so without
    them Obsidian renders broken."""
    _init_repo(tmp_path)
    (tmp_path / ".artmind").mkdir()
    vault.write_gitignore(tmp_path)
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "deck.pptx").write_bytes(b"binary")
    artifacts = tmp_path / "_derived" / "general" / "deck_artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "image-1.png").write_bytes(b"png")

    status = _git(tmp_path, "status", "--porcelain", "--untracked-files=all")
    assert "sources/deck.pptx" not in status
    assert "_derived/general/deck_artifacts/image-1.png" in status


def test_writing_the_gitignore_twice_does_not_duplicate_it(tmp_path):
    (tmp_path / ".artmind").mkdir()
    vault.write_gitignore(tmp_path)
    first = (tmp_path / ".gitignore").read_text()
    vault.write_gitignore(tmp_path)

    assert (tmp_path / ".gitignore").read_text() == first


def test_an_existing_gitignore_is_appended_to_not_replaced(tmp_path):
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".gitignore").write_text(".DS_Store\n")

    vault.write_gitignore(tmp_path)

    content = (tmp_path / ".gitignore").read_text()
    assert ".DS_Store" in content
    assert ".artmind/data/" in content


def test_scaffold_creates_the_vault_skeleton(tmp_path):
    from artmind.setup import scaffold_vault

    result = scaffold_vault(tmp_path)

    layout = vault.VaultLayout(tmp_path)
    assert layout.artmind_dir.is_dir()
    assert layout.schemas_dir.is_dir()
    assert layout.data_dir.is_dir()
    assert layout.logs_dir.is_dir()
    assert layout.meta_yaml.is_file()
    assert layout.config_env.is_file()
    assert result["vault"] == str(tmp_path)


def test_scaffold_seeds_starter_schemas_only(tmp_path):
    """A personal vault has no use for the banking demo corpus's domains, and
    offering domains with no data degrades the agent's routing."""
    from artmind.setup import scaffold_vault

    seeded = scaffold_vault(tmp_path)["schemas"]

    assert "general" in seeded
    assert not [s for s in seeded if s.startswith("banking")], seeded


def test_scaffold_never_overwrites_an_edited_schema(tmp_path):
    """Overwrite-always was safe for one reseeded run folder; here it would
    destroy authored work."""
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)
    schema = vault.VaultLayout(tmp_path).schemas_dir / "general_schema.yaml"
    schema.write_text("name: general\n# my edit\n")

    scaffold_vault(tmp_path)

    assert "# my edit" in schema.read_text()


def test_scaffold_never_overwrites_config_env(tmp_path):
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)
    config = vault.VaultLayout(tmp_path).config_env
    config.write_text("ARTMIND_KG_NEO4J_DATABASE=mine\n")

    scaffold_vault(tmp_path)

    assert config.read_text() == "ARTMIND_KG_NEO4J_DATABASE=mine\n"


def test_scaffold_writes_a_starter_vault_yaml(tmp_path):
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)

    import yaml
    manifest = yaml.safe_load(vault.VaultLayout(tmp_path).vault_yaml.read_text())
    assert manifest["ingest"]["trigger"] == "manual"
    assert manifest["ingest"]["mappings"] == []


def test_scaffold_symlinks_skills_to_the_installed_copy(tmp_path):
    """One canonical copy, so an artmind upgrade reaches every vault without
    re-seeding."""
    from artmind.setup import scaffold_vault

    scaffold_vault(tmp_path)

    linked = vault.VaultLayout(tmp_path).skills_dir / "artmind-query"
    assert linked.is_symlink()
    assert (linked / "SKILL.md").is_file()
