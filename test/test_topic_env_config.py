"""Tests for ARTMIND_TOPIC_MAX_DOCS env configuration.

Hermetic: no Neo4j, no LLM.  All Neo4j access is patched or bypassed.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind.cli import cli


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_result(max_docs: int) -> dict:
    return {"slug": "notice", "path": "x", "max_docs": max_docs}


# ── ARTMIND_TOPIC_MAX_DOCS ─────────────────────────────────────────────────────


def test_max_docs_env_default_used_when_flag_absent(monkeypatch, tmp_path):
    """ARTMIND_TOPIC_MAX_DOCS=7 is used when --maxDocs is not given."""
    monkeypatch.setenv("ARTMIND_TOPIC_MAX_DOCS", "7")
    with patch("artmind.topics.build.build_topic", return_value=_build_result(7)) as build:
        result = CliRunner().invoke(
            cli, ["topic", "build", "Notice", "--domain", "j", "--out", str(tmp_path), "--dryRun", "--compact"]
        )
    assert result.exit_code == 0, result.output
    _, kwargs = build.call_args
    assert kwargs["max_docs"] == 7


def test_max_docs_flag_overrides_env(monkeypatch, tmp_path):
    """An explicit --maxDocs flag wins over ARTMIND_TOPIC_MAX_DOCS."""
    monkeypatch.setenv("ARTMIND_TOPIC_MAX_DOCS", "7")
    with patch("artmind.topics.build.build_topic", return_value=_build_result(3)) as build:
        result = CliRunner().invoke(
            cli, ["topic", "build", "Notice", "--domain", "j", "--maxDocs", "3",
                  "--out", str(tmp_path), "--dryRun", "--compact"]
        )
    assert result.exit_code == 0, result.output
    _, kwargs = build.call_args
    assert kwargs["max_docs"] == 3


def test_max_docs_fallback_default_without_env(monkeypatch, tmp_path):
    """When ARTMIND_TOPIC_MAX_DOCS is absent the default is 150."""
    monkeypatch.delenv("ARTMIND_TOPIC_MAX_DOCS", raising=False)
    with patch("artmind.topics.build.build_topic", return_value=_build_result(150)) as build:
        result = CliRunner().invoke(
            cli, ["topic", "build", "Notice", "--domain", "j", "--out", str(tmp_path), "--dryRun", "--compact"]
        )
    assert result.exit_code == 0, result.output
    _, kwargs = build.call_args
    assert kwargs["max_docs"] == 150


def test_max_docs_env_non_int_raises_click_error_naming_variable(monkeypatch, tmp_path):
    """A non-integer ARTMIND_TOPIC_MAX_DOCS is a ClickException naming the variable."""
    monkeypatch.setenv("ARTMIND_TOPIC_MAX_DOCS", "banana")
    with patch("artmind.topics.build.build_topic", return_value=_build_result(12)):
        result = CliRunner().invoke(
            cli, ["topic", "build", "Notice", "--domain", "j", "--out", str(tmp_path), "--dryRun"]
        )
    assert result.exit_code == 1
    assert "ARTMIND_TOPIC_MAX_DOCS" in result.output


def test_max_docs_env_zero_raises_click_error_naming_range(monkeypatch, tmp_path):
    """An out-of-range value (0) is a ClickException naming the variable and allowed range."""
    monkeypatch.setenv("ARTMIND_TOPIC_MAX_DOCS", "0")
    with patch("artmind.topics.build.build_topic", return_value=_build_result(12)):
        result = CliRunner().invoke(
            cli, ["topic", "build", "Notice", "--domain", "j", "--out", str(tmp_path), "--dryRun"]
        )
    assert result.exit_code == 1
    assert "ARTMIND_TOPIC_MAX_DOCS" in result.output
    assert "1" in result.output and "300" in result.output


def test_max_docs_env_above_max_raises_click_error(monkeypatch, tmp_path):
    """301 is above the 1-300 allowed range."""
    monkeypatch.setenv("ARTMIND_TOPIC_MAX_DOCS", "301")
    with patch("artmind.topics.build.build_topic", return_value=_build_result(12)):
        result = CliRunner().invoke(
            cli, ["topic", "build", "Notice", "--domain", "j", "--out", str(tmp_path), "--dryRun"]
        )
    assert result.exit_code == 1
    assert "ARTMIND_TOPIC_MAX_DOCS" in result.output


def test_max_docs_env_boundary_values_accepted(monkeypatch, tmp_path):
    """Boundary values 1 and 300 are accepted."""
    for val in ("1", "300"):
        monkeypatch.setenv("ARTMIND_TOPIC_MAX_DOCS", val)
        with patch("artmind.topics.build.build_topic", return_value=_build_result(int(val))) as build:
            result = CliRunner().invoke(
                cli, ["topic", "build", "Notice", "--domain", "j", "--out", str(tmp_path), "--dryRun", "--compact"]
            )
        assert result.exit_code == 0, f"val={val}: {result.output}"
        _, kwargs = build.call_args
        assert kwargs["max_docs"] == int(val)


# ── config.env template ───────────────────────────────────────────────────────


def test_config_env_template_mentions_topic_max_docs():
    from artmind.setup import _CONFIG_ENV_TEMPLATE

    assert "ARTMIND_TOPIC_MAX_DOCS" in _CONFIG_ENV_TEMPLATE


def test_config_env_template_topic_max_docs_is_commented_out():
    from artmind.setup import _CONFIG_ENV_TEMPLATE

    for line in _CONFIG_ENV_TEMPLATE.splitlines():
        if "ARTMIND_TOPIC_MAX_DOCS" in line:
            assert line.lstrip().startswith("#"), f"Expected comment, got: {line!r}"
