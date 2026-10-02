"""The `artmind vault new` Click command. Behavior is tested in test_vault_new.py;
this covers the wiring: flags, confirmation, error display."""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from artmind import vault_new as vn
from artmind.cli import cli

from test_vault_new import world  # noqa: F401  (pytest fixture)


def _args(world, *extra):
    return ["vault", "new", "demo", "--dir", str(world.tmp / "vaults" / "demo"),
            "--noPlugins", "--noOpen", *extra]


def test_vault_new_creates_the_vault(world):
    result = CliRunner().invoke(cli, _args(world, "--yes"))

    assert result.exit_code == 0, result.output
    assert "Create vault demo:" in result.output
    assert "github   neurongraph/demo  (new, private)" in result.output
    assert "Vault demo is ready" in result.output
    assert world.sent("gh", "repo", "create") == [["gh", "repo", "create", "neurongraph/demo", "--private"]]


def test_vault_new_asks_before_creating_anything(world):
    result = CliRunner().invoke(cli, _args(world), input="n\n")

    assert result.exit_code != 0
    assert "Proceed?" in result.output
    assert world.sent("neo4j-manager", "create") == []
    assert not (world.tmp / "vaults" / "demo").exists()


def test_vault_new_join_flag(world):
    world.repos.add("neurongraph/demo")

    result = CliRunner().invoke(cli, _args(world, "--join", "--yes"))

    assert result.exit_code == 0, result.output
    assert "Join vault demo:" in result.output
    assert world.sent("gh", "repo", "clone")


def test_vault_new_shows_preflight_problems(world):
    world.repos.add("neurongraph/demo")

    result = CliRunner().invoke(cli, _args(world, "--yes"))

    assert result.exit_code == 1
    assert "already exists — pass --join" in result.output
    assert world.sent("neo4j-manager", "create") == []


def test_vault_new_passes_neo4j_flags(world):
    result = CliRunner().invoke(cli, _args(
        world, "--localOnly", "--neo4jUri", "neo4j+s://x.databases.neo4j.io",
        "--neo4jUser", "aura", "--neo4jPassword", "secret", "--yes",
    ))

    assert result.exit_code == 0, result.output
    assert world.sent("neo4j-manager") == []
    config = (world.tmp / "vaults" / "demo" / ".artmind" / "config.env").read_text()
    assert "ARTMIND_KG_NEO4J_URI=neo4j+s://x.databases.neo4j.io" in config
