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
