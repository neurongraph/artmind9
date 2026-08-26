"""Phase 5 (docs/redesign-phase-plan.md, "C"): the `curation` and
`originals` snapshot components, the dropped `registry` component, and the
manifest's `vault_commit`/`vault_dirty` fields. `registry` was covered by
test_unified_snapshot.py's older name; these tests cover what replaced it.
"""
import json
import tarfile
import zipfile

import pytest

import artmind.unified_snapshot as unified_snapshot


@pytest.fixture()
def env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    schemas_dir = home / "domains" / "schemas"
    schemas_dir.mkdir(parents=True)
    originals_dir = tmp_path / "data" / "originals"
    graph_snapshot_dir = tmp_path / "data" / "graph_snapshot"

    monkeypatch.setattr(unified_snapshot, "ARTMIND_HOME", home)
    monkeypatch.setattr(unified_snapshot, "DOMAIN_SCHEMAS_DIR", schemas_dir)
    monkeypatch.setattr(unified_snapshot, "ORIGINALS_DIR", originals_dir)
    monkeypatch.setattr(unified_snapshot, "GRAPH_SNAPSHOT_DIR", graph_snapshot_dir)

    import artmind.same_as as same_as

    monkeypatch.setattr(same_as, "SAME_AS_PATH", home / "same_as.yaml")

    return home, schemas_dir, originals_dir


# ── VALID_COMPONENTS / DEFAULT_COMPONENTS ────────────────────────────────────


def test_registry_is_no_longer_a_component():
    assert "registry" not in unified_snapshot.VALID_COMPONENTS
    assert "registry" not in unified_snapshot.DEFAULT_COMPONENTS


def test_originals_ships_in_the_default_set():
    """Deliberate and counter-intuitive per the phase plan: documents/originals/
    is the ONLY copy of an ingested binary and was in no snapshot before this."""
    assert "originals" in unified_snapshot.DEFAULT_COMPONENTS


def test_curation_is_valid_but_not_in_the_default_set():
    assert "curation" in unified_snapshot.VALID_COMPONENTS
    assert "curation" not in unified_snapshot.DEFAULT_COMPONENTS


# ── _archive_curation ─────────────────────────────────────────────────────────


def test_archive_curation_tolerates_missing_same_as_yaml(env, tmp_path):
    home, schemas_dir, _ = env
    (schemas_dir / "banking_schema.yaml").write_text("classes: []\n")

    archive_path, meta = unified_snapshot._archive_curation(tmp_path)

    assert meta["has_same_as"] is False
    assert meta["schema_count"] == 1
    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()
    assert "curation/domains/schemas/banking_schema.yaml" in names
    assert not any("same_as" in n for n in names)


def test_archive_curation_includes_same_as_when_present(env, tmp_path):
    home, schemas_dir, _ = env
    (home / "same_as.yaml").write_text("groups: []\n")

    archive_path, meta = unified_snapshot._archive_curation(tmp_path)

    assert meta["has_same_as"] is True
    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()
    assert "curation/same_as.yaml" in names


def test_archive_curation_never_touches_env(env, tmp_path):
    """Explicit files only, never a glob of the run folder -- .env sits right
    next to same_as.yaml in ARTMIND_HOME and must never be swept in."""
    home, schemas_dir, _ = env
    (home / ".env").write_text("ARTMIND_KG_NEO4J_PASSWORD=secret\n")
    (home / "same_as.yaml").write_text("groups: []\n")

    archive_path, _ = unified_snapshot._archive_curation(tmp_path)

    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()
    assert not any(".env" in n for n in names)


# ── _archive_originals ────────────────────────────────────────────────────────


def test_archive_originals_bundles_every_file(env, tmp_path):
    _, _, originals_dir = env
    originals_dir.mkdir(parents=True)
    (originals_dir / "deck.pptx").write_bytes(b"fake pptx")
    (originals_dir / "report.pdf").write_bytes(b"fake pdf")

    archive_path, meta = unified_snapshot._archive_originals(tmp_path)

    assert meta["file_count"] == 2
    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()
    assert any(n.endswith("deck.pptx") for n in names)
    assert any(n.endswith("report.pdf") for n in names)


def test_archive_originals_empty_when_dir_missing(env, tmp_path):
    archive_path, meta = unified_snapshot._archive_originals(tmp_path)
    assert meta["file_count"] == 0
    with tarfile.open(archive_path, "r:gz") as tar:
        assert tar.getnames() == []


# ── manifest ──────────────────────────────────────────────────────────────────


def test_manifest_carries_vault_commit_and_dirty(monkeypatch):
    monkeypatch.setattr(unified_snapshot.vault_git, "current_commit", lambda: "abc123")
    monkeypatch.setattr(unified_snapshot.vault_git, "is_dirty", lambda: True)

    manifest = unified_snapshot._create_manifest({})

    assert manifest["vault_commit"] == "abc123"
    assert manifest["vault_dirty"] is True


def test_manifest_vault_fields_none_without_a_configured_vault(monkeypatch):
    monkeypatch.setattr(unified_snapshot.vault_git, "current_commit", lambda: None)
    monkeypatch.setattr(unified_snapshot.vault_git, "is_dirty", lambda: None)

    manifest = unified_snapshot._create_manifest({})

    assert manifest["vault_commit"] is None
    assert manifest["vault_dirty"] is None


# ── create_snapshot end to end (graph/structured mocked, filesystem real) ────


def test_create_snapshot_never_contains_env(env, tmp_path, monkeypatch):
    home, schemas_dir, originals_dir = env
    (home / ".env").write_text("ARTMIND_KG_NEO4J_PASSWORD=secret\nARTMIND_OPENROUTER_API_KEY=xyz\n")
    (home / "same_as.yaml").write_text("groups: []\n")
    (schemas_dir / "general_schema.yaml").write_text("classes: []\n")
    originals_dir.mkdir(parents=True)
    (originals_dir / "deck.pptx").write_bytes(b"fake")

    monkeypatch.setattr(unified_snapshot, "_export_graph_inner", lambda: _fake_tar(tmp_path, "g"))
    monkeypatch.setattr(unified_snapshot, "_read_graph_snapshot", lambda p: {"meta": {}})
    monkeypatch.setattr(unified_snapshot, "_export_structured_inner", lambda: _fake_tar(tmp_path, "s"))
    monkeypatch.setattr(unified_snapshot.vault_git, "current_commit", lambda: None)
    monkeypatch.setattr(unified_snapshot.vault_git, "is_dirty", lambda: None)

    zip_path = unified_snapshot.create_snapshot(
        include={"graph", "structured", "curation", "originals"}
    )

    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert not any(".env" in n for n in names)
        manifest = json.loads(zf.read("manifest.json"))
        assert set(manifest["components"].keys()) == {"graph", "structured", "curation", "originals"}

        # Peek inside the curation/originals archives too, not just the zip's
        # own top level -- a leaked .env would hide one level down otherwise.
        import io

        curation_bytes = zf.read("curation.tar.gz")
        with tarfile.open(fileobj=io.BytesIO(curation_bytes), mode="r:gz") as tar:
            assert not any(".env" in n for n in tar.getnames())


def _fake_tar(tmp_path, tag):
    p = tmp_path / f"{tag}.tar.gz"
    with tarfile.open(p, "w:gz"):
        pass
    return p


# ── restore round-trip for curation/originals ────────────────────────────────


def _make_zip_with_curation_and_originals(tmp_path, home, originals_dir):
    curation_src = tmp_path / "curation_src"
    (curation_src / "domains" / "schemas").mkdir(parents=True)
    (curation_src / "same_as.yaml").write_text("groups: []\n")
    (curation_src / "domains" / "schemas" / "general_schema.yaml").write_text("classes: []\n")

    curation_tar = tmp_path / "curation.tar.gz"
    with tarfile.open(curation_tar, "w:gz") as tar:
        tar.add(curation_src / "same_as.yaml", arcname="curation/same_as.yaml")
        tar.add(
            curation_src / "domains" / "schemas" / "general_schema.yaml",
            arcname="curation/domains/schemas/general_schema.yaml",
        )

    originals_src = tmp_path / "originals_src"
    originals_src.mkdir()
    (originals_src / "deck.pptx").write_bytes(b"fake pptx")
    originals_tar = tmp_path / "originals.tar.gz"
    with tarfile.open(originals_tar, "w:gz") as tar:
        tar.add(originals_src / "deck.pptx", arcname="originals/deck.pptx")

    manifest = {
        "created_at": "2026-08-26T00:00:00",
        "version": 1,
        "components": {"curation": {}, "originals": {}},
    }
    zip_path = tmp_path / "snap.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.write(curation_tar, arcname="curation.tar.gz")
        zf.write(originals_tar, arcname="originals.tar.gz")
    return zip_path


def test_restore_curation_lands_files_under_artmind_home(env, tmp_path):
    home, schemas_dir, originals_dir = env
    zip_path = _make_zip_with_curation_and_originals(tmp_path, home, originals_dir)

    result = unified_snapshot.restore_snapshot_impl(zip_path, include={"curation"})

    assert (home / "same_as.yaml").read_text() == "groups: []\n"
    assert (home / "domains" / "schemas" / "general_schema.yaml").exists()
    assert result["details"]["curation"]["files_restored"] == 2


def test_restore_originals_lands_files_under_originals_dir(env, tmp_path):
    home, schemas_dir, originals_dir = env
    zip_path = _make_zip_with_curation_and_originals(tmp_path, home, originals_dir)

    result = unified_snapshot.restore_snapshot_impl(zip_path, include={"originals"})

    assert (originals_dir / "deck.pptx").read_bytes() == b"fake pptx"
    assert result["details"]["originals"]["file_count"] == 1
