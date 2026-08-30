"""`artmind init` and `artmind vault` (docs/vault.md)."""
from __future__ import annotations

import subprocess
from pathlib import Path

from click.testing import CliRunner

from artmind import vault
from artmind.cli import cli


def test_init_makes_the_current_directory_a_vault(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert vault.VaultLayout(tmp_path).artmind_dir.is_dir()
    assert vault.find_vault(tmp_path) == tmp_path.resolve()


def test_init_runs_git_init_when_the_directory_is_not_a_repo(tmp_path, monkeypatch):
    """The vault IS a git repo: identity, history and the ingest cursor all
    depend on it."""
    monkeypatch.chdir(tmp_path)

    CliRunner().invoke(cli, ["init"])

    assert (tmp_path / ".git").is_dir()


def test_init_leaves_an_existing_repo_alone(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "note.md").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)

    CliRunner().invoke(cli, ["init"])

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "first" in log


def test_init_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init"])
    schema = vault.VaultLayout(tmp_path).schemas_dir / "general_schema.yaml"
    schema.write_text("name: general\n# edited\n")

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert "# edited" in schema.read_text()


def test_init_accepts_an_explicit_path(tmp_path):
    target = tmp_path / "MyVault"
    target.mkdir()

    result = CliRunner().invoke(cli, ["init", str(target)])

    assert result.exit_code == 0, result.output
    assert (target / ".artmind").is_dir()
