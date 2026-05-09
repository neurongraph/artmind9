import json
import tarfile
from pathlib import Path

import pytest

from artmind.graph_snapshot import (
    _match_keys_for_node,
    _find_latest_snapshot,
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
