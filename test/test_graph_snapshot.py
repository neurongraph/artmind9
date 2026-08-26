import json
import tarfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from artmind.graph_snapshot import (
    _match_keys_for_node,
    _find_latest_snapshot,
    _read_snapshot,
    _export_relationships,
    _restore_nodes,
    _restore_relationships,
    import_graph,
)


class TestMatchKeysForNode:
    def test_no_entity_special_case_left(self):
        # Phase 5 stopped exporting :Entity at all (it's derived, not a
        # source) -- a node carrying that label among others still matches
        # by `id` like everything else, with no name/class/domain branch.
        labels = ["CHARACTER", "Entity"]
        props = {"name": "Elara", "entity_class": "CHARACTER", "_domain": "fiction", "id": "abc"}
        assert _match_keys_for_node(labels, props) == {"id": "abc"}

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
    def test_every_label_restores_verbatim_no_entity_special_case(self):
        """Phase 5 stopped exporting :Entity (derived, not a source) -- there
        is no label-reconstruction branch left to test; every base label
        restores exactly as exported."""
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
        # DocChunk/UserChat survive a round-trip. The three History labels
        # join in Phase 4 — they ARE the retired half of
        # Document/DocChunk/Observation, not a separate zone, so omitting
        # them would silently drop every retired document from a snapshot.
        # Phase 5 drops :Entity from the set entirely (sources only — see
        # BASE_LABELS) and adds :Synthesis pre-emptively for Phase 6.
        assert set(params["base_labels"]) == {
            "Document", "DocumentHistory",
            "DocChunk", "DocChunkHistory",
            "UserChat",
            "Observation", "ObservationHistory",
            "Synthesis",
        }
        assert "Entity" not in params["base_labels"]

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


def test_snapshots_export_sources_only_not_the_derived_projection():
    """Phase 5: sources only. :Entity (and :Conflict, and every projection-
    owned edge) is derived from :Observation and is deliberately excluded —
    a snapshot carrying a derived layer could carry a STALE one with no way
    for import to know. `import_graph` rebuilds the projection instead."""
    from artmind.graph_snapshot import BASE_LABELS

    assert "Observation" in BASE_LABELS
    assert "ObservationHistory" in BASE_LABELS
    assert "Entity" not in BASE_LABELS


class TestImportGraphRebuildPhase:
    """Phase 5 (docs/redesign-phase-plan.md, "B"): import_graph's final
    phase — docs reindex -> full projection rebuild -> embed sweep — must
    run automatically, in that order, for every graph restore. Not optional
    and not best-effort for the rebuild; the embed sweep's failure is the
    one documented exception (CLAUDE.md: never null an embedding)."""

    def _patch_common(self, monkeypatch, tmp_path):
        import artmind.graph_snapshot as gs

        snapshot_path = tmp_path / "snap.tar.gz"
        snapshot_path.write_text("")
        monkeypatch.setattr(gs, "_read_snapshot", lambda p: {"nodes": {}, "relationships": []})
        monkeypatch.setattr(gs, "_wipe_database", lambda session: None)
        monkeypatch.setattr(gs, "_setup_neo4j", lambda session, dim: None)
        monkeypatch.setattr(gs, "_restore_nodes", lambda session, nodes: {})
        monkeypatch.setattr(gs, "_restore_relationships", lambda session, rels: 0)
        return snapshot_path

    def _session_double(self, monkeypatch, stale_count=0):
        import artmind.graph_snapshot as gs

        session = MagicMock()
        session.execute_read.side_effect = lambda fn: fn(MagicMock())
        session.execute_write.side_effect = lambda fn: fn(MagicMock())
        session.run.return_value.single.return_value = {"n": stale_count}
        ctx = MagicMock()
        ctx.__enter__.return_value = session
        ctx.__exit__.return_value = False
        monkeypatch.setattr(gs, "neo4j_session", lambda *a, **k: ctx)
        return session

    def test_reindex_rebuild_and_sweep_all_run_in_order(self, tmp_path, monkeypatch):
        snapshot_path = self._patch_common(monkeypatch, tmp_path)
        self._session_double(monkeypatch, stale_count=0)

        order: list[str] = []
        monkeypatch.setattr("artmind.reindex.reindex", lambda: order.append("reindex") or {"registered": 1})
        monkeypatch.setattr(
            "artmind.projection.all_keys",
            lambda tx, domains=None: (order.append("all_keys") or {("alice", "PERSON", "banking")}),
        )
        monkeypatch.setattr(
            "artmind.projection.full_rebuild",
            lambda tx, domains=None: (order.append("full_rebuild") or {"entities": 1}),
        )
        monkeypatch.setattr(
            "artmind.ingest._sweep_embeddings",
            lambda domain, keys: order.append(f"sweep:{domain}") or 1,
        )

        result = import_graph(snapshot_path)

        assert order == ["reindex", "all_keys", "full_rebuild", "sweep:banking"]
        assert result["reindex"] == {"registered": 1}
        assert result["projection_rebuild"] == {"entities": 1}
        assert result["embedded"] == 1
        assert result["embedding_stale_remaining"] == 0

    def test_sweep_scoped_per_top_level_domain_family(self, tmp_path, monkeypatch):
        snapshot_path = self._patch_common(monkeypatch, tmp_path)
        self._session_double(monkeypatch, stale_count=0)

        monkeypatch.setattr("artmind.reindex.reindex", lambda: {})
        monkeypatch.setattr(
            "artmind.projection.all_keys",
            lambda tx, domains=None: {
                ("a", "PERSON", "banking.reference"),
                ("b", "PERSON", "banking.products"),
                ("c", "PERSON", "legal"),
            },
        )
        monkeypatch.setattr("artmind.projection.full_rebuild", lambda tx, domains=None: {})
        swept = []
        monkeypatch.setattr(
            "artmind.ingest._sweep_embeddings",
            lambda domain, keys: swept.append((domain, len(keys))) or len(keys),
        )

        import_graph(snapshot_path)

        swept_domains = {d for d, _ in swept}
        assert swept_domains == {"banking", "legal"}
        banking_count = dict(swept)["banking"]
        assert banking_count == 2  # both banking.reference and banking.products keys

    def test_reindex_failure_does_not_abort_the_rebuild(self, tmp_path, monkeypatch):
        """No vault configured (or any other reindex failure) must not skip
        the projection rebuild -- the graph is still what matters most."""
        snapshot_path = self._patch_common(monkeypatch, tmp_path)
        self._session_double(monkeypatch, stale_count=0)

        def _boom():
            raise RuntimeError("ARTMIND_VAULT_DIR is not configured")

        monkeypatch.setattr("artmind.reindex.reindex", _boom)
        rebuilt = []
        monkeypatch.setattr("artmind.projection.all_keys", lambda tx, domains=None: set())
        monkeypatch.setattr(
            "artmind.projection.full_rebuild", lambda tx, domains=None: rebuilt.append(True) or {}
        )
        monkeypatch.setattr("artmind.ingest._sweep_embeddings", lambda domain, keys: 0)

        result = import_graph(snapshot_path)

        assert rebuilt == [True]
        assert result["reindex"] is None
        assert "not configured" in result["reindex_error"]

    def test_stale_embeddings_remaining_is_reported_not_swallowed(self, tmp_path, monkeypatch):
        snapshot_path = self._patch_common(monkeypatch, tmp_path)
        self._session_double(monkeypatch, stale_count=3)

        monkeypatch.setattr("artmind.reindex.reindex", lambda: {})
        monkeypatch.setattr("artmind.projection.all_keys", lambda tx, domains=None: set())
        monkeypatch.setattr("artmind.projection.full_rebuild", lambda tx, domains=None: {})
        monkeypatch.setattr("artmind.ingest._sweep_embeddings", lambda domain, keys: 0)

        result = import_graph(snapshot_path)

        assert result["embedding_stale_remaining"] == 3
