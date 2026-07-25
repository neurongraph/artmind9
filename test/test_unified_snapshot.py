"""restore_snapshot_impl's post-structured-restore catalogue rebuild.

The structured-store catalogue (:Table/:TableColumn/:EntityClass in Neo4j,
see artmind/structured/catalogue.py) is a derived projection that
graph_snapshot.py deliberately never carries (see its
_export_relationships docstring) — those node/relationship types aren't
KG data, they're rebuilt from the SQLite registry. So restoring the
"structured" component of a unified snapshot must also re-run
project_catalogue() per domain, or Neo4j's copy stays stale/absent. These
tests are hermetic: project_catalogue and the registry lookup it depends on
are monkeypatched, no live Neo4j involved.
"""

import json
import zipfile

import artmind.unified_snapshot as unified_snapshot


def _make_zip(tmp_path, name, components: dict) -> "Path":
    zip_path = tmp_path / name
    manifest = {"created_at": "2026-07-25T00:00:00", "version": 1, "components": components}
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
    return zip_path


class TestCatalogueRebuildOnStructuredRestore:
    def test_rebuilds_catalogue_per_top_level_domain_after_structured_restore(
        self, tmp_path, monkeypatch
    ):
        zip_path = _make_zip(tmp_path, "snap.zip", {"structured": {}})

        monkeypatch.setattr(
            unified_snapshot,
            "_import_structured_inner",
            lambda path: {"table_count": 2},
        )

        import artmind.structured.registry as structured_registry
        import artmind.structured.catalogue as catalogue

        monkeypatch.setattr(
            structured_registry,
            "list_tables",
            lambda: [
                {"domain": "banking"},
                {"domain": "banking.retail"},  # rolls up under "banking"
                {"domain": "general"},
            ],
        )

        calls = []

        def fake_project_catalogue(domain):
            calls.append(domain)
            return {"tables": 1, "columns": 2, "mappings": 1}

        monkeypatch.setattr(catalogue, "project_catalogue", fake_project_catalogue)

        result = unified_snapshot.restore_snapshot_impl(zip_path, include={"structured"})

        assert sorted(calls) == ["banking", "general"]  # one call per top-level domain
        assert result["details"]["catalogue"] == {
            "tables": 2,
            "columns": 4,
            "mappings": 2,
            "domains": ["banking", "general"],
        }
        assert "catalogue" in result["components_restored"]
        assert result["success"] is True

    def test_catalogue_rebuild_failure_is_reported_but_not_fatal(self, tmp_path, monkeypatch):
        zip_path = _make_zip(tmp_path, "snap.zip", {"structured": {}})

        monkeypatch.setattr(
            unified_snapshot,
            "_import_structured_inner",
            lambda path: {"table_count": 1},
        )

        import artmind.structured.registry as structured_registry
        import artmind.structured.catalogue as catalogue

        monkeypatch.setattr(structured_registry, "list_tables", lambda: [{"domain": "banking"}])

        def raising_project_catalogue(domain):
            raise RuntimeError("Neo4j unreachable")

        monkeypatch.setattr(catalogue, "project_catalogue", raising_project_catalogue)

        result = unified_snapshot.restore_snapshot_impl(zip_path, include={"structured"})

        assert result["success"] is False
        assert any("Catalogue rebuild failed" in e for e in result["errors"])
        # The structured component itself still restored fine.
        assert "structured" in result["components_restored"]
        assert "catalogue" not in result["components_restored"]

    def test_no_catalogue_rebuild_when_structured_not_restored(self, tmp_path, monkeypatch):
        zip_path = _make_zip(tmp_path, "snap.zip", {})

        import artmind.structured.catalogue as catalogue

        calls = []
        monkeypatch.setattr(
            catalogue, "project_catalogue", lambda domain: calls.append(domain)
        )

        result = unified_snapshot.restore_snapshot_impl(zip_path, include=None)

        assert calls == []
        assert result["components_restored"] == []
