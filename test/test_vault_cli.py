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


def test_init_next_steps_point_at_ingest_async(tmp_path, monkeypatch):
    """Step 5 of the onboarding review (docs/onboarding-review.md) found the
    command to actually ingest a vault's documents wasn't obvious -- point at
    it directly instead of leaving the user to find `ingest async` on their own."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    assert "artmind ingest async ." in result.output


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


def test_vault_reports_the_active_vault(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init"])

    result = CliRunner().invoke(cli, ["vault"])

    assert result.exit_code == 0, result.output
    assert str(tmp_path.resolve()) in result.output


def test_init_remote_flag_configures_origin_non_interactively(tmp_path, monkeypatch):
    """--remote works without --interactive -- e.g. from a script."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(
        cli, ["init", "--remote", "https://github.com/example/vault.git"]
    )

    assert result.exit_code == 0, result.output
    remotes = subprocess.run(
        ["git", "remote", "-v"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "https://github.com/example/vault.git" in remotes
    assert "Remote:   origin -> https://github.com/example/vault.git" in result.output


def test_init_default_is_non_interactive(tmp_path, monkeypatch):
    """No flags, no stdin available (CliRunner's default) -- must still exit 0
    with the plain-placeholder config.env, exactly like before --interactive
    existed. This is the automation path (`just dev-install`): nobody is at
    the keyboard, so prompting by default here would break it."""
    monkeypatch.chdir(tmp_path)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0, result.output
    config = vault.VaultLayout(tmp_path).config_env.read_text()
    assert "ARTMIND_KG_NEO4J_USERNAME=neo4j" in config


def test_init_interactive_prompts_for_neo4j_connection_and_remote(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    answers = "\n".join([
        "neo4j+s://mydb.databases.neo4j.io",  # Neo4j URI
        "myuser",  # Neo4j username
        "hunter2",  # Neo4j password
        "mydb",  # Neo4j database
        "y",  # push after each ingest?
        "https://github.com/example/vault.git",  # remote URL
    ]) + "\n"

    result = CliRunner().invoke(cli, ["init", "--interactive"], input=answers)

    assert result.exit_code == 0, result.output
    config = vault.VaultLayout(tmp_path).config_env.read_text()
    assert "ARTMIND_KG_NEO4J_URI=neo4j+s://mydb.databases.neo4j.io" in config
    assert "ARTMIND_KG_NEO4J_USERNAME=myuser" in config
    assert "ARTMIND_KG_NEO4J_PASSWORD=hunter2" in config
    assert "ARTMIND_KG_NEO4J_DATABASE=mydb" in config
    assert "ARTMIND_VAULT_GIT_PUSH=1" in config.splitlines()
    remotes = subprocess.run(
        ["git", "remote", "-v"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "https://github.com/example/vault.git" in remotes


def test_init_interactive_skips_prompts_when_config_env_already_exists(tmp_path, monkeypatch):
    """Re-running `init --interactive` on an already-configured vault must not
    prompt at all -- there's no answers to gather, and a leftover config.env
    may hold hand-edited (e.g. Aura) values nothing here should touch."""
    monkeypatch.chdir(tmp_path)
    CliRunner().invoke(cli, ["init"])
    config = vault.VaultLayout(tmp_path).config_env
    config.write_text("ARTMIND_KG_NEO4J_DATABASE=mine\n")

    # No input supplied: if this tried to prompt, it would abort on EOF.
    result = CliRunner().invoke(cli, ["init", "--interactive"], input="")

    assert result.exit_code == 0, result.output
    assert config.read_text() == "ARTMIND_KG_NEO4J_DATABASE=mine\n"


def test_vault_outside_a_vault_explains_rather_than_guessing(tmp_path, monkeypatch):
    """`git status` outside a repo, not a silent default."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ARTMIND_VAULT", raising=False)
    monkeypatch.delenv("ARTMIND_HOME", raising=False)

    result = CliRunner().invoke(cli, ["vault"])

    assert result.exit_code != 0
    assert "artmind init" in result.output
