"""Tests for `artmind domains` CLI commands using test/schemas/test_schema.yaml."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from artmind.cli import cli

TEST_SCHEMA = Path(__file__).parent / "schemas" / "test_schema.yaml"
DOMAIN_NAME = "test"


@pytest.fixture(autouse=True)
def clean_domain(tmp_path, monkeypatch):
    """Point DOMAIN_SCHEMAS_DIR at a temp directory for each test."""
    import artmind.cli as cli_module
    import paths as paths_module

    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()

    monkeypatch.setattr(cli_module, "DOMAIN_SCHEMAS_DIR", schemas_dir)
    monkeypatch.setattr(paths_module, "DOMAIN_SCHEMAS_DIR", schemas_dir)
    yield schemas_dir


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def domain_added(runner, clean_domain):
    """Pre-add the test domain so commands that depend on it can run."""
    result = runner.invoke(cli, ["domains", "add", str(TEST_SCHEMA)])
    assert result.exit_code == 0, result.output
    return clean_domain


# ── list ──────────────────────────────────────────────────────────────────────

class TestList:
    def test_list_includes_general_when_empty(self, runner):
        result = runner.invoke(cli, ["domains", "list"])
        assert result.exit_code == 0
        assert "general" in result.output

    def test_list_shows_added_domain(self, runner, domain_added):
        result = runner.invoke(cli, ["domains", "list"])
        assert result.exit_code == 0
        assert DOMAIN_NAME in result.output


# ── add ───────────────────────────────────────────────────────────────────────

class TestAdd:
    def test_add_copies_schema_file(self, runner, clean_domain):
        result = runner.invoke(cli, ["domains", "add", str(TEST_SCHEMA)])
        assert result.exit_code == 0
        assert (clean_domain / f"{DOMAIN_NAME}_schema.yaml").exists()

    def test_add_output_confirms_domain_name(self, runner, clean_domain):
        result = runner.invoke(cli, ["domains", "add", str(TEST_SCHEMA)])
        assert result.exit_code == 0
        assert DOMAIN_NAME in result.output

    def test_add_nonexistent_file_fails(self, runner):
        result = runner.invoke(cli, ["domains", "add", "no_such_file.yaml"])
        assert result.exit_code != 0


# ── entities ──────────────────────────────────────────────────────────────────

class TestEntities:
    def test_entities_lists_expected(self, runner, domain_added):
        result = runner.invoke(cli, ["domains", "entities", DOMAIN_NAME])
        assert result.exit_code == 0
        for entity in ["Document", "Title", "Author", "Date", "Content", "Section"]:
            assert entity in result.output

    def test_entities_unknown_domain_fails(self, runner):
        result = runner.invoke(cli, ["domains", "entities", "nonexistent"])
        assert result.exit_code != 0


# ── relationships ─────────────────────────────────────────────────────────────

class TestRelationships:
    def test_relationships_lists_expected(self, runner, domain_added):
        result = runner.invoke(cli, ["domains", "relationships", DOMAIN_NAME])
        assert result.exit_code == 0
        for rel in ["has_title", "authored_by", "written_on", "discusses", "contains", "references"]:
            assert rel in result.output

    def test_relationships_unknown_domain_fails(self, runner):
        result = runner.invoke(cli, ["domains", "relationships", "nonexistent"])
        assert result.exit_code != 0


# ── delete ────────────────────────────────────────────────────────────────────

class TestDelete:
    def test_delete_removes_schema_file(self, runner, domain_added):
        result = runner.invoke(cli, ["domains", "delete", DOMAIN_NAME])
        assert result.exit_code == 0
        assert not (domain_added / f"{DOMAIN_NAME}_schema.yaml").exists()

    def test_delete_output_confirms_domain_name(self, runner, domain_added):
        result = runner.invoke(cli, ["domains", "delete", DOMAIN_NAME])
        assert result.exit_code == 0
        assert DOMAIN_NAME in result.output

    def test_delete_nonexistent_domain_fails(self, runner):
        result = runner.invoke(cli, ["domains", "delete", "nonexistent"])
        assert result.exit_code != 0

    def test_delete_then_list_removes_domain(self, runner, domain_added):
        runner.invoke(cli, ["domains", "delete", DOMAIN_NAME])
        result = runner.invoke(cli, ["domains", "list"])
        assert DOMAIN_NAME not in result.output
