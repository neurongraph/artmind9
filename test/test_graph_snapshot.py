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
    _restored_stale_entity_keys,
    _sweep_stale_embeddings,
    import_graph,
)


class TestMatchKeysForNode:
    def test_entity_matches_by_underscore_id_not_id(self):
        # Phase 9: :Entity is exported again (see PROJECTED_LABELS), and it's
        # MERGEd in the live graph on `_id` (projection.rebuild_key), not
        # `id` -- a relationship pointing at a restored Entity has to be
        # re-matched the same way, or AGGREGATES/RELATES_TO/CONFLICT_OF onto
        # it would silently fail to attach on restore.
        labels = ["CHARACTER", "Entity"]
        props = {
            "name": "Elara", "entity_class": "CHARACTER", "_domain": "fiction",
            "_id": "abc123", "id": "should-be-ignored-for-entities",
        }
        assert _match_keys_for_node(labels, props) == {"_id": "abc123"}

    def test_entity_with_no_underscore_id_has_no_match_keys(self):
        assert _match_keys_for_node(["Entity"], {"name": "Elara"}) == {}

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
    def test_single_label_node_restores_under_its_bucket_label(self):
        fake = FakeSession(records=[])
        _restore_nodes(fake, {"Document": [{"id": "doc1"}]})
        assert "CREATE (n:Document)" in fake.calls[0][0]

    def test_node_with_no_stored_labels_falls_back_to_bucket_label(self):
        """A pre-Phase-9 snapshot (or any bucket whose label set never
        varies) has no `labels` key on the node dict at all."""
        fake = FakeSession(records=[])
        _restore_nodes(fake, {"Document": [{"id": "doc1"}]})
        cypher, params = fake.calls[0]
        assert "CREATE (n:Document)" in cypher
        assert params["props"] == {"id": "doc1"}

    def test_entity_restores_with_its_full_multi_label_set(self):
        """An entity node carries `:Entity:<CLASS>` in the live graph
        (projection.rebuild_key's apoc.create.addLabels) -- restoring it
        under just the "Entity" bucket label would silently drop the class
        label every entity-scoped query relies on."""
        fake = FakeSession(records=[])
        _restore_nodes(fake, {
            "Entity": [{"_id": "abc", "name": "Elara", "labels": ["Entity", "CHARACTER"]}],
        })
        cypher, params = fake.calls[0]
        assert "CREATE (n:Entity:CHARACTER)" in cypher
        assert "labels" not in params["props"]
        assert params["props"] == {"_id": "abc", "name": "Elara"}


class TestExportRelationships:
    def test_scopes_cypher_to_base_and_projected_labels_by_default(self):
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
        # Phase 9 widens the default scope to PROJECTED_LABELS too, so
        # RELATES_TO/AGGREGATES/SAME_AS/CONFLICT_OF/EVIDENCE travel with the
        # snapshot alongside :Entity/:Conflict/:ProjectionState.
        assert set(params["base_labels"]) == {
            "Document", "DocumentHistory",
            "DocChunk", "DocChunkHistory",
            "UserChat",
            "Observation", "ObservationHistory",
            "Synthesis",
            "Entity", "Conflict", "ProjectionState",
        }

    def test_labels_param_still_overridable(self):
        """Callers that want the old sources-only scope (e.g. a partial
        export) can still ask for exactly BASE_LABELS."""
        from artmind.graph_snapshot import BASE_LABELS

        fake = FakeSession(records=[])
        _export_relationships(fake, labels=BASE_LABELS)
        _, params = fake.calls[0]
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


def test_base_labels_still_sources_only_projected_labels_hold_the_derived_layer():
    """Phase 5 kept BASE_LABELS sources-only. Phase 9 adds PROJECTED_LABELS
    alongside it for the derived layer (:Entity/:Conflict/:ProjectionState),
    now that :ProjectionState gives import a real way to detect a stale
    restored copy instead of having to assume the worst and always rebuild."""
    from artmind.graph_snapshot import BASE_LABELS, PROJECTED_LABELS

    assert "Observation" in BASE_LABELS
    assert "ObservationHistory" in BASE_LABELS
    assert "Entity" not in BASE_LABELS

    assert PROJECTED_LABELS == ("Entity", "Conflict", "ProjectionState")


class TestImportGraphRebuildPhase:
    """import_graph's final phase — docs reindex -> projection -> embed
    sweep — always runs, in that order, for every graph restore. These tests
    cover the "projection can't be trusted" case (no restored :Entity data,
    i.e. node_counts is empty here), where the projection step is always a
    full rebuild — not optional, not best-effort. See
    TestImportGraphFastRestore below for the Phase 9 case where a restored
    :Entity layer is trusted instead. The embed sweep's own failure is the
    one documented exception either way (CLAUDE.md: never null an
    embedding)."""

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
            lambda tx, domains=None, **kw: (order.append("full_rebuild") or {"entities": 1}),
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
        monkeypatch.setattr("artmind.projection.full_rebuild", lambda tx, domains=None, **kw: {})
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
            "artmind.projection.full_rebuild", lambda tx, domains=None, **kw: rebuilt.append(True) or {}
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
        monkeypatch.setattr("artmind.projection.full_rebuild", lambda tx, domains=None, **kw: {})
        monkeypatch.setattr("artmind.ingest._sweep_embeddings", lambda domain, keys: 0)

        result = import_graph(snapshot_path)

        assert result["embedding_stale_remaining"] == 3


class TestImportGraphFastRestore:
    """Phase 9: when the snapshot carries a restored :Entity layer AND it's
    provably in sync with same_as.yaml/the domain schemas (its restored
    :ProjectionState hashes match what's current), import_graph trusts it
    and skips the full rebuild — entity ids are deterministic, so a rebuild
    would reproduce the same graph anyway. Any uncertainty falls back to the
    unconditional rebuild from TestImportGraphRebuildPhase above."""

    def _patch_common(self, monkeypatch, tmp_path, *, entity_count: int):
        import artmind.graph_snapshot as gs

        snapshot_path = tmp_path / "snap.tar.gz"
        snapshot_path.write_text("")
        monkeypatch.setattr(gs, "_read_snapshot", lambda p: {"nodes": {}, "relationships": []})
        monkeypatch.setattr(gs, "_wipe_database", lambda session: None)
        monkeypatch.setattr(gs, "_setup_neo4j", lambda session, dim: None)
        monkeypatch.setattr(
            gs, "_restore_nodes", lambda session, nodes: {"Entity": entity_count}
        )
        monkeypatch.setattr(gs, "_restore_relationships", lambda session, rels: 0)
        monkeypatch.setattr("artmind.reindex.reindex", lambda: {})
        return snapshot_path

    def _session_double(self, monkeypatch, *, stale_count=0, stale_key_rows=None):
        import artmind.graph_snapshot as gs

        session = MagicMock()
        # Unlike TestImportGraphRebuildPhase's double, `tx` here must be the
        # same configured mock as `session` (not a disconnected fresh one) --
        # _restored_stale_entity_keys(tx) really calls tx.run(...).data(),
        # it isn't monkeypatched away like projection.full_rebuild is.
        session.execute_read.side_effect = lambda fn: fn(session)
        session.execute_write.side_effect = lambda fn: fn(session)
        session.run.return_value.single.return_value = {"n": stale_count}
        session.run.return_value.data.return_value = stale_key_rows or []
        ctx = MagicMock()
        ctx.__enter__.return_value = session
        ctx.__exit__.return_value = False
        monkeypatch.setattr(gs, "neo4j_session", lambda *a, **k: ctx)
        return session

    def test_no_drift_skips_the_full_rebuild(self, tmp_path, monkeypatch):
        snapshot_path = self._patch_common(monkeypatch, tmp_path, entity_count=8072)
        self._session_double(monkeypatch, stale_count=0)

        monkeypatch.setattr(
            "artmind.projection.read_state",
            lambda tx: {"same_as_hash": "same", "schema_hash": "schema"},
        )
        monkeypatch.setattr("artmind.same_as.content_hash", lambda: "same")
        monkeypatch.setattr("artmind.projection.schema_set_hash", lambda: "schema")
        rebuilt = []
        monkeypatch.setattr(
            "artmind.projection.full_rebuild", lambda tx, domains=None, **kw: rebuilt.append(True)
        )

        result = import_graph(snapshot_path)

        assert rebuilt == []
        assert result["projection_rebuild_skipped"] is True
        assert result["projection_rebuild"] == {"skipped": True, "reason": "no drift detected"}

    def test_same_as_drift_forces_a_rebuild(self, tmp_path, monkeypatch):
        snapshot_path = self._patch_common(monkeypatch, tmp_path, entity_count=8072)
        self._session_double(monkeypatch, stale_count=0)

        monkeypatch.setattr(
            "artmind.projection.read_state",
            lambda tx: {"same_as_hash": "stale-hash", "schema_hash": "schema"},
        )
        monkeypatch.setattr("artmind.same_as.content_hash", lambda: "current-hash")
        monkeypatch.setattr("artmind.projection.schema_set_hash", lambda: "schema")
        monkeypatch.setattr("artmind.projection.all_keys", lambda tx, domains=None: set())
        rebuilt = []
        monkeypatch.setattr(
            "artmind.projection.full_rebuild",
            lambda tx, domains=None, **kw: rebuilt.append(True) or {},
        )

        result = import_graph(snapshot_path)

        assert rebuilt == [True]
        assert result["projection_rebuild_skipped"] is False

    def test_schema_drift_forces_a_rebuild(self, tmp_path, monkeypatch):
        snapshot_path = self._patch_common(monkeypatch, tmp_path, entity_count=8072)
        self._session_double(monkeypatch, stale_count=0)

        monkeypatch.setattr(
            "artmind.projection.read_state",
            lambda tx: {"same_as_hash": "same", "schema_hash": "old-schema"},
        )
        monkeypatch.setattr("artmind.same_as.content_hash", lambda: "same")
        monkeypatch.setattr("artmind.projection.schema_set_hash", lambda: "new-schema")
        monkeypatch.setattr("artmind.projection.all_keys", lambda tx, domains=None: set())
        rebuilt = []
        monkeypatch.setattr(
            "artmind.projection.full_rebuild",
            lambda tx, domains=None, **kw: rebuilt.append(True) or {},
        )

        result = import_graph(snapshot_path)

        assert rebuilt == [True]

    def test_no_projection_state_forces_a_rebuild(self, tmp_path, monkeypatch):
        """Entities restored, but no :ProjectionState alongside them (an
        older-format snapshot minus its hashes) -- can't vouch for the copy,
        so fall back to rebuilding it."""
        snapshot_path = self._patch_common(monkeypatch, tmp_path, entity_count=8072)
        self._session_double(monkeypatch, stale_count=0)

        monkeypatch.setattr("artmind.projection.read_state", lambda tx: None)
        monkeypatch.setattr("artmind.projection.all_keys", lambda tx, domains=None: set())
        rebuilt = []
        monkeypatch.setattr(
            "artmind.projection.full_rebuild",
            lambda tx, domains=None, **kw: rebuilt.append(True) or {},
        )

        result = import_graph(snapshot_path)

        assert rebuilt == [True]

    def test_force_rebuild_true_rebuilds_despite_no_drift(self, tmp_path, monkeypatch):
        snapshot_path = self._patch_common(monkeypatch, tmp_path, entity_count=8072)
        self._session_double(monkeypatch, stale_count=0)

        monkeypatch.setattr(
            "artmind.projection.read_state",
            lambda tx: {"same_as_hash": "same", "schema_hash": "schema"},
        )
        monkeypatch.setattr("artmind.same_as.content_hash", lambda: "same")
        monkeypatch.setattr("artmind.projection.schema_set_hash", lambda: "schema")
        monkeypatch.setattr("artmind.projection.all_keys", lambda tx, domains=None: set())
        rebuilt = []
        monkeypatch.setattr(
            "artmind.projection.full_rebuild",
            lambda tx, domains=None, **kw: rebuilt.append(True) or {},
        )

        result = import_graph(snapshot_path, force_rebuild=True)

        assert rebuilt == [True]
        assert result["projection_rebuild_skipped"] is False

    def test_force_rebuild_false_skips_despite_drift_and_warns(self, tmp_path, monkeypatch):
        snapshot_path = self._patch_common(monkeypatch, tmp_path, entity_count=8072)
        self._session_double(monkeypatch, stale_count=0)

        monkeypatch.setattr(
            "artmind.projection.read_state",
            lambda tx: {"same_as_hash": "stale-hash", "schema_hash": "schema"},
        )
        monkeypatch.setattr("artmind.same_as.content_hash", lambda: "current-hash")
        monkeypatch.setattr("artmind.projection.schema_set_hash", lambda: "schema")
        rebuilt = []
        monkeypatch.setattr(
            "artmind.projection.full_rebuild",
            lambda tx, domains=None, **kw: rebuilt.append(True) or {},
        )

        result = import_graph(snapshot_path, force_rebuild=False)

        assert rebuilt == []
        assert result["projection_rebuild_skipped"] is True
        assert "same_as.yaml changed" in result["projection_rebuild"]["reason"]

    def test_no_entity_data_falls_back_to_rebuild_even_with_force_rebuild_none(
        self, tmp_path, monkeypatch
    ):
        """An older snapshot (or a graph-only restore with --only excluding
        the projected labels) restores zero entities -- auto mode must not
        mistake "nothing restored" for "nothing changed"."""
        snapshot_path = self._patch_common(monkeypatch, tmp_path, entity_count=0)
        self._session_double(monkeypatch, stale_count=0)

        monkeypatch.setattr("artmind.projection.all_keys", lambda tx, domains=None: set())
        rebuilt = []
        monkeypatch.setattr(
            "artmind.projection.full_rebuild",
            lambda tx, domains=None, **kw: rebuilt.append(True) or {},
        )

        result = import_graph(snapshot_path)

        assert rebuilt == [True]

    def test_fast_path_sweeps_only_entities_flagged_stale(self, tmp_path, monkeypatch):
        """The fast (no-rebuild) path still has to honor an embedding that
        was already stale *at export time* -- it just scopes the sweep to
        those entities directly instead of walking every key via
        projection.all_keys."""
        snapshot_path = self._patch_common(monkeypatch, tmp_path, entity_count=2)
        self._session_double(
            monkeypatch, stale_count=0,
            stale_key_rows=[{"key": "alice|PERSON|banking"}, {"key": "bad-key-shape"}],
        )

        monkeypatch.setattr(
            "artmind.projection.read_state",
            lambda tx: {"same_as_hash": "same", "schema_hash": "schema"},
        )
        monkeypatch.setattr("artmind.same_as.content_hash", lambda: "same")
        monkeypatch.setattr("artmind.projection.schema_set_hash", lambda: "schema")
        swept = []
        monkeypatch.setattr(
            "artmind.ingest._sweep_embeddings",
            lambda domain, keys: swept.append((domain, keys)) or len(keys),
        )

        result = import_graph(snapshot_path)

        # The malformed row ("bad-key-shape" has no "|") is dropped rather
        # than crashing the restore.
        assert swept == [("banking", [("alice", "PERSON", "banking")])]
        assert result["embedded"] == 1


class TestSweepStaleEmbeddings:
    def test_groups_by_top_level_domain_family(self, monkeypatch, tmp_path):
        import artmind.graph_snapshot as gs

        session = MagicMock()
        session.run.return_value.single.return_value = {"n": 0}
        ctx = MagicMock()
        ctx.__enter__.return_value = session
        ctx.__exit__.return_value = False
        monkeypatch.setattr(gs, "neo4j_session", lambda *a, **k: ctx)

        swept = []
        monkeypatch.setattr(
            "artmind.ingest._sweep_embeddings",
            lambda domain, keys: swept.append((domain, len(keys))) or len(keys),
        )

        keys = [
            ("a", "PERSON", "banking.reference"),
            ("b", "PERSON", "banking.products"),
            ("c", "PERSON", "legal"),
        ]
        embedded_total, stale_remaining = _sweep_stale_embeddings(keys)

        assert dict(swept) == {"banking": 2, "legal": 1}
        assert embedded_total == 3
        assert stale_remaining == 0


class TestRestoredStaleEntityKeys:
    def test_parses_well_formed_keys_and_drops_malformed_ones(self):
        session = MagicMock()
        session.run.return_value.data.return_value = [
            {"key": "alice|PERSON|banking"},
            {"key": "malformed"},
            {"key": None},
        ]
        assert _restored_stale_entity_keys(session) == [("alice", "PERSON", "banking")]
