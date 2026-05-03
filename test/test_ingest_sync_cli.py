"""Tests for `artmind ingest sync` CLI command."""

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind.cli import cli

_OK_RESULT = {"filename": "sample.txt", "status": "ok"}
_FAIL_RESULT = {"filename": "sample.txt", "status": "failed", "error": "boom"}


@pytest.fixture()
def runner():
    return CliRunner()


@pytest.fixture()
def sample_file(tmp_path) -> Path:
    f = tmp_path / "sample.txt"
    f.write_text("hello")
    return f


@pytest.fixture()
def sample_dir(tmp_path) -> Path:
    d = tmp_path / "docs"
    d.mkdir()
    (d / "a.txt").write_text("aaa")
    (d / "b.txt").write_text("bbb")
    return d


# ── single file ───────────────────────────────────────────────────────────────

class TestIngestSyncFile:
    def test_exits_zero_on_success(self, runner, sample_file):
        with patch("artmind.cli.ingest_file", return_value=_OK_RESULT), \
             patch("artmind.cli.ingest_to_kg"):
            result = runner.invoke(cli, ["ingest", "sync", str(sample_file), "--domain", "general"])
        assert result.exit_code == 0

    def test_calls_ingest_file_with_domain(self, runner, sample_file):
        with patch("artmind.cli.ingest_file", return_value=_OK_RESULT) as mock_ingest, \
             patch("artmind.cli.ingest_to_kg"):
            runner.invoke(cli, ["ingest", "sync", str(sample_file), "--domain", "general"])
        mock_ingest.assert_called_once()
        _, kwargs = mock_ingest.call_args
        assert mock_ingest.call_args[0][2] == "general"

    def test_calls_ingest_to_kg_on_ok(self, runner, sample_file):
        with patch("artmind.cli.ingest_file", return_value=_OK_RESULT), \
             patch("artmind.cli.ingest_to_kg") as mock_kg:
            runner.invoke(cli, ["ingest", "sync", str(sample_file), "--domain", "general"])
        mock_kg.assert_called_once()

    def test_skips_ingest_to_kg_on_failed(self, runner, sample_file):
        with patch("artmind.cli.ingest_file", return_value=_FAIL_RESULT), \
             patch("artmind.cli.ingest_to_kg") as mock_kg:
            result = runner.invoke(cli, ["ingest", "sync", str(sample_file), "--domain", "general"])
        mock_kg.assert_not_called()
        assert result.exit_code == 0

    def test_nonexistent_path_fails(self, runner):
        result = runner.invoke(cli, ["ingest", "sync", "/no/such/file.txt", "--domain", "general"])
        assert result.exit_code != 0


# ── directory ─────────────────────────────────────────────────────────────────

class TestIngestSyncDirectory:
    def test_processes_all_files(self, runner, sample_dir):
        with patch("artmind.cli.ingest_file", return_value=_OK_RESULT) as mock_ingest, \
             patch("artmind.cli.ingest_to_kg"):
            runner.invoke(cli, ["ingest", "sync", str(sample_dir), "--domain", "general"])
        assert mock_ingest.call_count == 2

    def test_counts_failures_without_crashing(self, runner, sample_dir):
        with patch("artmind.cli.ingest_file", side_effect=RuntimeError("fail")), \
             patch("artmind.cli.ingest_to_kg"):
            result = runner.invoke(cli, ["ingest", "sync", str(sample_dir), "--domain", "general"])
        assert result.exit_code == 0


# ── domain prompt fallback ────────────────────────────────────────────────────

class TestIngestSyncDomainPrompt:
    def test_prompts_when_domain_omitted(self, runner, sample_file):
        with patch("artmind.cli.ingest_file", return_value=_OK_RESULT), \
             patch("artmind.cli.ingest_to_kg"), \
             patch("artmind.cli._prompt_for_domain", return_value="general") as mock_prompt:
            result = runner.invoke(cli, ["ingest", "sync", str(sample_file)])
        mock_prompt.assert_called_once()
        assert result.exit_code == 0
