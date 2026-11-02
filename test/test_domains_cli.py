"""Tests for `artmind domains` CLI commands using test/schemas/test_schema.yaml."""

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


# ── JSON on stdout, the former text on stderr (cli-uniformity spec 3.2) ──────

import json


def _envelope_of(result):
    payload = json.loads(result.stdout)
    assert {"command", "warnings", "next"} <= set(payload)
    return payload


class TestJsonOutput:
    def test_list(self, runner, domain_added):
        result = runner.invoke(cli, ["domains", "list", "--compact"])
        payload = _envelope_of(result)
        assert payload["command"] == "domains list" and DOMAIN_NAME in payload["domains"]
        assert payload["rows_total"] == len(payload["domains"]) and payload["truncated"] is False
        assert DOMAIN_NAME in result.stderr and "\n" not in result.stdout.strip()

    def test_add_and_delete(self, runner, clean_domain):
        added = runner.invoke(cli, ["domains", "add", str(TEST_SCHEMA), "--compact"])
        payload = _envelope_of(added)
        assert payload["domain"] == DOMAIN_NAME and payload["path"].endswith(f"{DOMAIN_NAME}_schema.yaml")
        assert f"Domain '{DOMAIN_NAME}' added" in added.stderr
        deleted = runner.invoke(cli, ["domains", "delete", DOMAIN_NAME, "--compact"])
        assert _envelope_of(deleted)["deleted"] is True
        assert f"Domain '{DOMAIN_NAME}' deleted" in deleted.stderr

    @pytest.mark.parametrize("kind", ["entities", "properties", "relationships"])
    def test_prompts_carry_the_text_in_prompt(self, runner, domain_added, kind):
        result = runner.invoke(cli, ["domains", f"{kind}-prompt", DOMAIN_NAME, "--compact"])
        assert result.exit_code == 0, result.output
        payload = _envelope_of(result)
        assert payload["domain"] == DOMAIN_NAME and payload["prompt"].strip()
        assert payload["prompt"] in result.stderr

    def test_validate_ok(self, runner, domain_added):
        result = runner.invoke(cli, ["domains", "validate", "--domain", DOMAIN_NAME, "--compact"])
        payload = _envelope_of(result)
        assert result.exit_code == 0 and payload["ok"] is True and payload["violations"] == {}
        assert "All schemas valid." in result.stderr

    def test_validate_failure_keeps_exit_code_and_prints_json(self, runner, clean_domain):
        (clean_domain / "bad_schema.yaml").write_text("name: bad\nentity_types: []\n")
        result = runner.invoke(cli, ["domains", "validate", "--domain", "bad", "--compact"])
        payload = _envelope_of(result)
        assert result.exit_code == 1 and payload["ok"] is False and payload["violations"]["bad"]
        assert "ERROR" in result.stderr

    def test_harmonize_reports_each_child(self, runner, monkeypatch):
        import artmind.cli as cli_module
        from artmind.harmonizer import HarmonizeResult

        seen = {}

        def fake_all(dry_run=False):
            seen["dry_run"] = dry_run
            return [HarmonizeResult("fiction.thriller", "dry_run", added=["PERSON"]),
                    HarmonizeResult("fiction.noir", "in_sync")]

        monkeypatch.setattr(cli_module, "harmonize_all", fake_all)
        result = runner.invoke(cli, ["domains", "harmonize", "--dry-run", "--compact"])
        payload = _envelope_of(result)
        assert seen == {"dry_run": True} and payload["dry_run"] is True
        assert payload["results"] == [
            {"domain": "fiction.thriller", "status": "dry_run", "added": ["PERSON"], "error": None},
            {"domain": "fiction.noir", "status": "in_sync", "added": [], "error": None},
        ]
        assert "would add entity types" in result.stderr

    def test_harmonize_with_no_children(self, runner, monkeypatch):
        import artmind.cli as cli_module

        monkeypatch.setattr(cli_module, "harmonize_all", lambda dry_run=False: [])
        result = runner.invoke(cli, ["domains", "harmonize", "--compact"])
        assert _envelope_of(result)["results"] == [] and "No child schemas found." in result.stderr
