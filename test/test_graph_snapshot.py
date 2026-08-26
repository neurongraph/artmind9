import json
import tarfile
from pathlib import Path

import pytest

from artmind.graph_snapshot import (
    _match_keys_for_node,
    _find_latest_snapshot,
    _read_snapshot,
    _export_relationships,
    _restore_nodes,
    _restore_relationships,
)


class TestMatchKeysForNode:
    def test_entity_uses_name_class_domain(self):
        # Entity carries `_domain` (Phase 4's `_`-prefix), not `domain` —
        # exactly the property name every other label uses, which is why
        # Entity needs its own branch here at all.
        labels = ["CHARACTER", "Entity"]
        props = {"name": "Elara", "entity_class": "CHARACTER", "_domain": "fiction", "_id": "abc"}
        assert _match_keys_for_node(labels, props) == {
            "name": "Elara",
            "entity_class": "CHARACTER",
            "_domain": "fiction",
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


class FakeResult:
    def __init__(self, records):
        self._records = records

    def __iter__(self):
        return iter(self._records)


class FakeSession:
    def __init__(self, records=None):
        self.calls = []
        self._records = records or []

    def run(self, cypher, **params):
        self.calls.append((cypher, params))
        return FakeResult(self._records)


class TestRestoreNodes:
    def test_entity_keeps_its_class_label(self):
        fake = FakeSession(records=[])
        _restore_nodes(fake, {"Entity": [
            {"labels": ["CHARACTER", "Entity"], "name": "Holmes", "entity_class": "CHARACTER"},
        ]})
        cypher = fake.calls[0][0]
        assert "CREATE (n:CHARACTER:Entity)" in cypher

    def test_entity_without_stored_labels_rebuilds_from_entity_class(self):
        """A snapshot missing `labels` (old format, hand-edited, foreign) used to
        restore a bare :Entity. Nothing in artmind can create one — _upsert_entity
        always writes `<CLASS>:Entity` — and readers key off the class label, so
        `entity_listing` would never surface such a node again."""
        fake = FakeSession(records=[])
        _restore_nodes(fake, {"Entity": [
            {"name": "Holmes", "entity_class": "CHARACTER"},
        ]})
        cypher = fake.calls[0][0]
        assert "CREATE (n:CHARACTER:Entity)" in cypher
        assert "CREATE (n:Entity)" not in cypher

    def test_entity_with_no_class_at_all_falls_back_to_unknown(self):
        """Never a bare :Entity — _sanitize_label's UNKNOWN fallback applies here
        the same way it does on the ingest path."""
        fake = FakeSession(records=[])
        _restore_nodes(fake, {"Entity": [{"name": "Mystery"}]})
        cypher = fake.calls[0][0]
        assert "CREATE (n:UNKNOWN:Entity)" in cypher

    def test_non_entity_label_is_used_verbatim(self):
        fake = FakeSession(records=[])
        _restore_nodes(fake, {"Document": [{"id": "doc1"}]})
        assert "CREATE (n:Document)" in fake.calls[0][0]


class TestExportRelationships:
    def test_scopes_cypher_to_base_labels(self):
        fake = FakeSession(records=[])
        _export_relationships(fake)
        assert len(fake.calls) == 1
        cypher, params = fake.calls[0]
        assert "any(l IN labels(s) WHERE l IN $base_labels)" in cypher
        assert "any(l IN labels(e) WHERE l IN $base_labels)" in cypher
        # :Observation joins the set in Phase 3 — its EXTRACTED_FROM edges to
        # DocChunk/UserChat, and the projection's AGGREGATES edges, both have
        # to survive a round-trip. The three History labels join in Phase 4 —
        # they ARE the retired half of Document/DocChunk/Observation, not a
        # separate zone, so omitting them would silently drop every retired
        # document from a snapshot.
        assert set(params["base_labels"]) == {
            "Document", "DocChunk", "Entity", "UserChat", "Observation",
            "DocumentHistory", "DocChunkHistory", "ObservationHistory",
        }

    def test_builds_relationship_dict_from_kg_nodes(self):
        records = [{
            "start_labels": ["Document"],
            "start_props": {"id": "doc1"},
            "rel_type": "HAS_CHUNK",
            "rel_props": {"weight": 1},
            "end_labels": ["DocChunk"],
            "end_props": {"id": "chunk1"},
        }]
        fake = FakeSession(records=records)
        result = _export_relationships(fake)
        assert result == [{
            "type": "HAS_CHUNK",
            "start_labels": ["Document"],
            "start_match": {"id": "doc1"},
            "end_labels": ["DocChunk"],
            "end_match": {"id": "chunk1"},
            "properties": {"weight": 1},
        }]


class TestRestoreRelationships:
    def test_skips_relationship_with_no_match_keys_without_querying(self):
        """A catalogue-style relationship (e.g. HAS_COLUMN between :Table/
        :TableColumn nodes) that slipped into an old snapshot has empty
        start_match/end_match since those node types were never restored.
        An empty WHERE clause would be a Cypher syntax error, so this must
        be skipped before ever calling session.run."""
        fake = FakeSession()
        relationships = [
            {"type": "HAS_COLUMN", "start_match": {}, "end_match": {}, "properties": {}},
        ]
        count = _restore_relationships(fake, relationships)
        assert count == 0
        assert fake.calls == []

    def test_restores_relationship_with_valid_match_keys(self):
        fake = FakeSession()
        relationships = [
            {
                "type": "HAS_CHUNK",
                "start_match": {"id": "doc1"},
                "end_match": {"id": "chunk1"},
                "properties": {},
            },
        ]
        count = _restore_relationships(fake, relationships)
        assert count == 1
        assert len(fake.calls) == 1


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


def test_snapshots_export_observations_alongside_the_projection():
    """A snapshot carrying :Entity but not :Observation would import entities
    that nothing asserts — and the first rebuild, which runs inside the next
    commit, would delete every one of them.

    Phase 5 inverts this properly (export sources, rebuild on import). Until
    then, both halves have to travel together."""
    from artmind.graph_snapshot import BASE_LABELS

    assert "Observation" in BASE_LABELS
    assert "Entity" in BASE_LABELS
