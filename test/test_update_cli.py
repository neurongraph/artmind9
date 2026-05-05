# test/test_update_cli.py
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from artmind.cli import cli


@pytest.fixture()
def runner():
    return CliRunner()


def test_update_draft_returns_json(runner):
    draft_result = {
        "session_id": "abc123",
        "extracted_entities": [
            {"temp_id": "e0", "name": "Alice", "entity_class": "PERSON", "properties": {}}
        ],
        "extracted_relationships": [],
        "candidates_per_entity": [{"entity": "Alice", "temp_id": "e0", "top_n": []}],
    }
    with patch("artmind.cli.update_backend.draft_update", return_value=draft_result):
        result = runner.invoke(cli, [
            "update", "draft",
            "--domain", "general",
            "--text", "Alice is a person",
        ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["session_id"] == "abc123"
    assert len(data["extracted_entities"]) == 1


def test_update_draft_with_existing_session(runner):
    draft_result = {
        "session_id": "existing-session",
        "extracted_entities": [],
        "extracted_relationships": [],
        "candidates_per_entity": [],
    }
    with patch("artmind.cli.update_backend.draft_update", return_value=draft_result) as mock:
        runner.invoke(cli, [
            "update", "draft",
            "--domain", "general",
            "--text", "Some fact",
            "--session", "existing-session",
        ])
    mock.assert_called_once_with(
        domain="general",
        text="Some fact",
        session_id="existing-session",
        user_id=mock.call_args.kwargs["user_id"],
    )


def test_update_confirm_returns_json(runner):
    confirm_result = {
        "nodes_created": 1,
        "nodes_updated": 0,
        "relationships_written": 0,
        "user_chat_id": "chat001",
    }
    resolutions = json.dumps([
        {"entity_temp_id": "e0", "action": "create", "node_id": None}
    ])
    with patch("artmind.cli.update_backend.confirm_update", return_value=confirm_result):
        result = runner.invoke(cli, [
            "update", "confirm",
            "--session", "abc123",
            "--resolutions", resolutions,
        ])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["nodes_created"] == 1
    assert data["user_chat_id"] == "chat001"


def test_update_confirm_fails_gracefully_on_missing_draft(runner):
    with patch("artmind.cli.update_backend.confirm_update",
               side_effect=ValueError("No pending draft")):
        result = runner.invoke(cli, [
            "update", "confirm",
            "--session", "bad-session",
            "--resolutions", "[]",
        ])
    assert result.exit_code != 0


def test_update_history_outputs_table(runner):
    sessions = [
        {
            "session_id": "abc123", "domain": "general",
            "created_by": "alice@example.com", "created_at": "2026-05-05T10:00:00",
            "status": "confirmed", "input_count": 2, "excerpt": "Alice is CEO",
        }
    ]
    with patch("artmind.cli.update_backend._list_update_sessions", return_value=sessions):
        result = runner.invoke(cli, ["update", "history"])
    assert result.exit_code == 0, result.output
    assert "abc123" in result.output
    assert "general" in result.output


def test_update_export_sequential_calls_backend(runner, tmp_path):
    mock_written = [tmp_path / "session_abc12345.md"]
    (tmp_path / "session_abc12345.md").write_text("# Session abc123")
    with patch("artmind.cli.update_backend.export_chats", return_value=mock_written) as mock:
        result = runner.invoke(cli, [
            "update", "export",
            "--format", "sequential",
            "--output", str(tmp_path),
        ])
    assert result.exit_code == 0, result.output
    mock.assert_called_once_with(
        domain=None, format="sequential", output_dir=Path(str(tmp_path))
    )
    assert "session_abc12345.md" in result.output


def test_update_export_by_entity_with_domain_filter(runner, tmp_path):
    with patch("artmind.cli.update_backend.export_chats", return_value=[]) as mock:
        runner.invoke(cli, [
            "update", "export",
            "--domain", "fiction",
            "--format", "by-entity",
            "--output", str(tmp_path),
        ])
    mock.assert_called_once_with(
        domain="fiction", format="by-entity", output_dir=Path(str(tmp_path))
    )
