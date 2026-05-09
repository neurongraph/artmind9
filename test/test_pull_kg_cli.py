"""Tests for `artmind ingest pull-kg` CLI command."""

from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind.cli import cli


@pytest.fixture()
def runner():
    return CliRunner()


class TestPullKgCli:
    def test_success_shows_summary(self, runner):
        summary = {"pulled_count": 3, "domain": "sales", "repo_url": "https://github.com/acme/repo.git", "conflicts": []}
        with patch("artmind.cli.pull_kg_fn", return_value=summary):
            result = runner.invoke(cli, [
                "ingest", "pull-kg",
                "--repo", "https://github.com/acme/repo.git",
                "--repo-path", "data/kg/sales",
                "--domain", "sales",
            ])
        assert result.exit_code == 0
        assert "3" in result.output

    def test_missing_repo_fails(self, runner):
        result = runner.invoke(cli, [
            "ingest", "pull-kg",
            "--repo-path", "data/kg/sales",
            "--domain", "sales",
        ])
        assert result.exit_code != 0

    def test_conflict_shows_error(self, runner):
        with patch("artmind.cli.pull_kg_fn", side_effect=RuntimeError("conflict(s) with existing local folders: doc_a")):
            result = runner.invoke(cli, [
                "ingest", "pull-kg",
                "--repo", "https://github.com/acme/repo.git",
                "--repo-path", "data/kg/sales",
                "--domain", "sales",
            ])
        assert result.exit_code != 0
        assert "conflict" in result.output

    def test_git_error_shows_message(self, runner):
        with patch("artmind.cli.pull_kg_fn", side_effect=RuntimeError("git clone failed: Authentication failed")):
            result = runner.invoke(cli, [
                "ingest", "pull-kg",
                "--repo", "https://github.com/acme/repo.git",
                "--repo-path", "data/kg/sales",
                "--domain", "sales",
            ])
        assert result.exit_code != 0
        assert "Authentication" in result.output
