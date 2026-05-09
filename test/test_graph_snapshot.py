import json
import tarfile
from pathlib import Path

import pytest

from artmind.graph_snapshot import (
    _match_keys_for_node,
    _find_latest_snapshot,
    _read_snapshot,
)


class TestMatchKeysForNode:
    def test_entity_uses_name_class_domain(self):
        labels = ["CHARACTER", "Entity"]
        props = {"name": "Elara", "entity_class": "CHARACTER", "domain": "fiction", "id": "abc"}
        assert _match_keys_for_node(labels, props) == {
            "name": "Elara",
            "entity_class": "CHARACTER",
            "domain": "fiction",
        }

    def test_document_uses_id(self):
        labels = ["Document"]
        props = {"id": "doc1", "name": "test.pdf", "domain": "fiction"}
        assert _match_keys_for_node(labels, props) == {"id": "doc1"}

    def test_docchunk_uses_id(self):
        labels = ["DocChunk"]
        props = {"id": "chunk1", "doc_id": "doc1", "text": "hello"}
        assert _match_keys_for_node(labels, props) == {"id": "chunk1"}

    def test_userchat_uses_id(self):
        labels = ["UserChat"]
        props = {"id": "chat1", "raw_text": "hello"}
        assert _match_keys_for_node(labels, props) == {"id": "chat1"}

    def test_unknown_label_falls_back_to_id(self):
        labels = ["SomeNewLabel"]
        props = {"id": "x1", "foo": "bar"}
        assert _match_keys_for_node(labels, props) == {"id": "x1"}


class TestFindLatestSnapshot:
    def test_returns_none_when_dir_missing(self, tmp_path, monkeypatch):
        import artmind.graph_snapshot as mod
        monkeypatch.setattr(mod, "GRAPH_SNAPSHOT_DIR", tmp_path / "nonexistent")
        assert _find_latest_snapshot() is None

    def test_returns_none_when_dir_empty(self, tmp_path, monkeypatch):
        import artmind.graph_snapshot as mod
        monkeypatch.setattr(mod, "GRAPH_SNAPSHOT_DIR", tmp_path)
        assert _find_latest_snapshot() is None

    def test_returns_latest_by_name(self, tmp_path, monkeypatch):
        import artmind.graph_snapshot as mod
        monkeypatch.setattr(mod, "GRAPH_SNAPSHOT_DIR", tmp_path)
        (tmp_path / "snapshot_2026-05-01_100000.tar.gz").write_text("")
        (tmp_path / "snapshot_2026-05-09_140000.tar.gz").write_text("")
        (tmp_path / "snapshot_2026-05-05_120000.tar.gz").write_text("")
        result = _find_latest_snapshot()
        assert result.name == "snapshot_2026-05-09_140000.tar.gz"

    def test_ignores_non_snapshot_files(self, tmp_path, monkeypatch):
        import artmind.graph_snapshot as mod
        monkeypatch.setattr(mod, "GRAPH_SNAPSHOT_DIR", tmp_path)
        (tmp_path / "random_file.tar.gz").write_text("")
        (tmp_path / "snapshot_2026-05-01_100000.tar.gz").write_text("")
        result = _find_latest_snapshot()
        assert result.name == "snapshot_2026-05-01_100000.tar.gz"


class TestReadSnapshot:
    def test_reads_valid_tar_gz(self, tmp_path):
        snapshot_data = {
            "meta": {"exported_at": "2026-05-09T14:00:00", "node_counts": {}, "relationship_count": 0},
            "schema": {},
            "nodes": {"Document": [], "DocChunk": [], "Entity": [], "UserChat": []},
            "relationships": [],
        }
        tar_path = tmp_path / "snapshot_2026-05-09_140000.tar.gz"
        json_path = tmp_path / "snapshot.json"
        json_path.write_text(json.dumps(snapshot_data), encoding="utf-8")
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(json_path, arcname="snapshot.json")
        json_path.unlink()

        result = _read_snapshot(tar_path)
        assert result["meta"]["exported_at"] == "2026-05-09T14:00:00"
        assert result["nodes"]["Document"] == []

    def test_raises_on_missing_snapshot_json(self, tmp_path):
        tar_path = tmp_path / "bad.tar.gz"
        json_path = tmp_path / "other.json"
        json_path.write_text("{}")
        with tarfile.open(tar_path, "w:gz") as tar:
            tar.add(json_path, arcname="other.json")
        json_path.unlink()

        with pytest.raises(ValueError, match="snapshot.json"):
            _read_snapshot(tar_path)


from unittest.mock import patch
from click.testing import CliRunner
from artmind.cli import cli


class TestSessionCloseCli:
    def test_exports_and_shows_summary(self, tmp_path):
        runner = CliRunner()
        fake_file = tmp_path / "snapshot_2026-05-09_140000.tar.gz"
        fake_file.write_bytes(b"x" * 1_500_000)
        with patch("artmind.cli.export_graph", return_value=fake_file) as mock_export:
            result = runner.invoke(cli, ["session", "close"])
        assert result.exit_code == 0, result.output
        mock_export.assert_called_once()
        assert "snapshot_2026-05-09_140000.tar.gz" in result.output


class TestSessionInitiateCli:
    def test_prompts_and_imports(self):
        runner = CliRunner()
        summary = {
            "snapshot": "snapshot_2026-05-09_140000.tar.gz",
            "node_counts": {"Document": 2, "DocChunk": 10, "Entity": 50, "UserChat": 1},
            "relationship_count": 100,
            "elapsed_seconds": 3.5,
        }
        with patch("artmind.cli.import_graph", return_value=summary):
            result = runner.invoke(cli, ["session", "initiate", "--yes"])
        assert result.exit_code == 0, result.output
        assert "Document" in result.output
        assert "100" in result.output

    def test_aborts_without_confirmation(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["session", "initiate"], input="n\n")
        assert result.exit_code != 0 or "Aborted" in result.output

    def test_uses_explicit_snapshot_path(self, tmp_path):
        runner = CliRunner()
        fake_snapshot = tmp_path / "custom.tar.gz"
        fake_snapshot.write_text("")
        summary = {
            "snapshot": "custom.tar.gz",
            "node_counts": {"Document": 1},
            "relationship_count": 5,
            "elapsed_seconds": 1.0,
        }
        with patch("artmind.cli.import_graph", return_value=summary) as mock_import:
            result = runner.invoke(
                cli, ["session", "initiate", "--yes", "--snapshot", str(fake_snapshot)]
            )
        assert result.exit_code == 0, result.output
        mock_import.assert_called_once()
        call_args = mock_import.call_args
        assert str(call_args[0][0]) == str(fake_snapshot)
