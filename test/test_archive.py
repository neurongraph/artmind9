"""Tests for artmind.archive: docs archive / restore-from-archive / archived
(docs/redesign-phase-plan.md, Phase 5 "A"). Neo4j is mocked per CLAUDE.md's
testing guidance (assert on parameters sent / queries run, and give every
fake session a working `execute_write`); the vault and archive root are real
temp directories with a real git repo, exercising `vault_git` for real.
"""
import json
import subprocess
from unittest.mock import MagicMock

import pytest

import artmind.archive as archive


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    _init_git_repo(vault)

    archive_root = tmp_path / "archive_root"
    kg_dir = tmp_path / "data" / "kg"
    originals = tmp_path / "data" / "originals"
    kg_dir.mkdir(parents=True)
    originals.mkdir(parents=True)

    monkeypatch.setattr(archive, "ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr(archive, "ARTMIND_ARCHIVE_DIR", archive_root)
    monkeypatch.setattr(archive, "KG_DIR", kg_dir)
    monkeypatch.setattr(archive, "ORIGINALS_DIR", originals)

    import artmind.document_identity as di
    import artmind.vault_git as vg

    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr(vg, "ARTMIND_VAULT_DIR", vault)

    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")

    return vault, archive_root, kg_dir, originals


class _Tx:
    def __init__(self):
        self.calls = []

    def run(self, cypher, **kw):
        self.calls.append((cypher, kw))
        result = MagicMock()
        result.single.return_value = {"n": 2}
        result.data.return_value = [{"key": "alice|PERSON|general"}]
        return result


def _mock_session(monkeypatch, tx):
    session = MagicMock()
    session.execute_write.side_effect = lambda fn, *a, **k: fn(tx, *a, **k)
    ctx = MagicMock()
    ctx.__enter__.return_value = session
    ctx.__exit__.return_value = False
    monkeypatch.setattr(archive, "neo4j_session", lambda *a, **k: ctx)


# ── _delete_document_tx ──────────────────────────────────────────────────────


def test_delete_document_tx_removes_both_labels_and_rebuilds(monkeypatch):
    tx = _Tx()
    monkeypatch.setattr(
        "artmind.projection.keys_for_document",
        lambda tx, doc_id: {("alice", "PERSON", "general")},
    )
    monkeypatch.setattr(
        "artmind.projection.rebuild", lambda tx, keys, **kw: {"rebuilt": sorted(keys)}
    )

    result = archive._delete_document_tx(tx, "doc-1")

    obs_deletes = [c for c, kw in tx.calls if "Observation OR o:ObservationHistory" in c and "DETACH DELETE o" in c]
    chunk_deletes = [c for c, kw in tx.calls if "DocChunk OR c:DocChunkHistory" in c]
    doc_deletes = [c for c, kw in tx.calls if "Document OR d:DocumentHistory" in c and "DETACH DELETE d" in c]
    assert len(obs_deletes) == 1
    assert len(chunk_deletes) == 1
    assert len(doc_deletes) == 1
    assert result["observations_deleted"] == 2
    assert result["keys"] == [("alice", "PERSON", "general")]
    # No :DocumentArchived label anywhere -- archive removes, it doesn't relabel.
    assert not any("Archived" in c for c, kw in tx.calls)


# ── index ─────────────────────────────────────────────────────────────────────


def test_list_archived_reads_from_index_not_filesystem(env):
    vault, archive_root, kg_dir, originals = env
    assert archive.list_archived() == []

    archive._append_index({"_artmind_id": "a", "domain": "general"})
    archive._append_index({"_artmind_id": "b", "domain": "banking"})

    entries = archive.list_archived()
    assert [e["_artmind_id"] for e in entries] == ["a", "b"]


# ── archive_document ─────────────────────────────────────────────────────────


def test_archive_document_bundles_removes_and_indexes(env, monkeypatch):
    vault, archive_root, kg_dir, originals = env

    doc = vault / "notes" / "policy.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("---\n_artmind_id: doc-1\ntitle: Policy\n---\n\nBody.\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=vault, check=True)

    doc_kg = kg_dir / "general" / "policy"
    doc_kg.mkdir(parents=True)
    (doc_kg / "document.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(archive, "resolve_document_id", lambda name, domain: "doc-1")
    monkeypatch.setattr(
        archive, "_document_info",
        lambda doc_id: {"path": "notes/policy.md", "version": 2, "name": "policy.md"},
    )
    monkeypatch.setattr(archive, "_observation_valid_time_span", lambda doc_id: ("2026-01-01", "2026-02-01"))

    tx = _Tx()
    _mock_session(monkeypatch, tx)
    monkeypatch.setattr("artmind.projection.keys_for_document", lambda tx, doc_id: set())
    monkeypatch.setattr("artmind.projection.rebuild", lambda tx, keys, **kw: {})

    result = archive.archive_document("general", "policy")

    bundle_dir = archive_root / "doc-1"
    assert result["bundle_dir"] == str(bundle_dir)
    assert (bundle_dir / "document.md").exists()
    assert (bundle_dir / "kg" / "general" / "policy" / "document.json").exists()
    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    assert manifest["_artmind_id"] == "doc-1"
    assert manifest["domain"] == "general"
    assert manifest["version"] == 2
    assert manifest["valid_from"] == "2026-01-01"

    # index recorded it
    assert [e["_artmind_id"] for e in archive.list_archived()] == ["doc-1"]

    # vault file is gone, and it was a real git commit
    assert not doc.exists()
    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "archive policy" in log

    assert result["git_committed"] is True


def test_archive_document_includes_and_deletes_the_original_binary(env, monkeypatch):
    vault, archive_root, kg_dir, originals = env

    doc = vault / "_derived" / "banking" / "deck.md"
    doc.parent.mkdir(parents=True)
    doc.write_text(
        "---\n_artmind_id: doc-2\n_source_type: pptx\ntitle: Deck\n---\n\nBody.\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=vault, check=True)

    original = originals / "deck.pptx"
    original.write_bytes(b"fake pptx bytes")

    monkeypatch.setattr(archive, "resolve_document_id", lambda name, domain: "doc-2")
    monkeypatch.setattr(
        archive, "_document_info",
        lambda doc_id: {"path": "_derived/banking/deck.md", "version": 1, "name": "deck.md"},
    )
    monkeypatch.setattr(archive, "_observation_valid_time_span", lambda doc_id: (None, None))

    tx = _Tx()
    _mock_session(monkeypatch, tx)
    monkeypatch.setattr("artmind.projection.keys_for_document", lambda tx, doc_id: set())
    monkeypatch.setattr("artmind.projection.rebuild", lambda tx, keys, **kw: {})

    result = archive.archive_document("banking", "deck")

    bundle_dir = archive_root / "doc-2"
    assert (bundle_dir / "original.pptx").read_bytes() == b"fake pptx bytes"
    assert not original.exists()  # data-dir copy deleted too (confirmed default)
    manifest = json.loads((bundle_dir / "manifest.json").read_text())
    assert manifest["has_original_binary"] is True
    assert manifest["source_type"] == "pptx"


def test_archive_document_raises_when_not_found(env, monkeypatch):
    monkeypatch.setattr(archive, "resolve_document_id", lambda name, domain: None)
    with pytest.raises(ValueError, match="No document matching"):
        archive.archive_document("general", "nope")


# ── restore_from_archive ──────────────────────────────────────────────────────


def _seed_bundle(archive_root, artmind_id, *, domain="general", vault_path="notes/policy.md", body="Body.\n"):
    bundle_dir = archive_root / artmind_id
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "document.md").write_text(
        f"---\n_artmind_id: {artmind_id}\ntitle: Policy\n---\n\n{body}", encoding="utf-8"
    )
    manifest = {
        "_artmind_id": artmind_id,
        "domain": domain,
        "original_vault_path": vault_path,
        "source_type": "md",
        "has_original_binary": False,
    }
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle_dir


def test_restore_from_archive_writes_vault_file_and_retires(env, monkeypatch):
    vault, archive_root, kg_dir, originals = env
    _init_git_repo(vault)  # already a repo from fixture; re-init is a no-op-ish, harmless
    bundle_dir = _seed_bundle(archive_root, "doc-1")

    monkeypatch.setattr(archive, "_document_info", lambda doc_id: {})
    committed_calls = []
    retire_calls = []
    monkeypatch.setattr("artmind.ingest.commit_to_graph", lambda *a, **k: (committed_calls.append(a) or True))
    monkeypatch.setattr(
        "artmind.lifecycle.retire_document",
        lambda doc_id, domain=None: retire_calls.append((doc_id, domain)) or {"observations": 1},
    )

    doc_kg = bundle_dir / "kg" / "general" / "policy"
    doc_kg.mkdir(parents=True)
    (doc_kg / "document.json").write_text(json.dumps({"id": "doc-1"}), encoding="utf-8")

    result = archive.restore_from_archive("doc-1")

    restored = vault / "notes" / "policy.md"
    assert restored.exists()
    assert result["restored_path"] == str(restored)
    assert result["committed"] is True
    assert retire_calls == [("doc-1", "general")]

    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "restore-from-archive doc-1" in log


def test_restore_from_archive_refuses_when_id_already_live(env, monkeypatch):
    archive_root = env[1]
    _seed_bundle(archive_root, "doc-1")
    monkeypatch.setattr(archive, "_document_info", lambda doc_id: {"id": "doc-1"})

    with pytest.raises(archive.ArchiveCollision, match="already live"):
        archive.restore_from_archive("doc-1")


def test_restore_from_archive_refuses_when_path_holds_a_different_file(env, monkeypatch):
    vault, archive_root, kg_dir, originals = env
    bundle_dir = _seed_bundle(archive_root, "doc-1")

    conflicting = vault / "notes" / "policy.md"
    conflicting.parent.mkdir(parents=True)
    conflicting.write_text("a totally different file\n", encoding="utf-8")

    monkeypatch.setattr(archive, "_document_info", lambda doc_id: {})

    with pytest.raises(archive.ArchiveCollision, match="already holds a different file"):
        archive.restore_from_archive("doc-1")


def test_restore_from_archive_to_path_escapes_the_path_collision(env, monkeypatch):
    vault, archive_root, kg_dir, originals = env
    bundle_dir = _seed_bundle(archive_root, "doc-1")

    conflicting = vault / "notes" / "policy.md"
    conflicting.parent.mkdir(parents=True)
    conflicting.write_text("a totally different file\n", encoding="utf-8")

    monkeypatch.setattr(archive, "_document_info", lambda doc_id: {})

    result = archive.restore_from_archive("doc-1", to_path="notes/policy-restored.md")
    assert (vault / "notes" / "policy-restored.md").exists()
    assert conflicting.read_text() == "a totally different file\n"  # untouched
