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

    import artmind.ingest as ing

    monkeypatch.setattr(ing, "KG_DIR", kg_dir)  # `_locate_staged` (a moved note's folder) reads it

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


def _registry_rows(kg_dir):
    """The registry rows -- `kg_dir` is `<tmp>/data/kg`, the registry `<tmp>/registry.db`."""
    import sqlite3

    db_file = kg_dir.parents[1] / "registry.db"
    if not db_file.exists():
        return []
    conn = sqlite3.connect(db_file)
    try:
        return conn.execute("SELECT artmind_id, path FROM documents").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _stub_archive_graph(monkeypatch, doc_id, path, name):
    monkeypatch.setattr(archive, "resolve_document_id", lambda n, d: doc_id)
    monkeypatch.setattr(
        archive, "_document_info", lambda i: {"path": path, "version": 1, "name": name}
    )
    monkeypatch.setattr(archive, "_observation_valid_time_span", lambda i: (None, None))
    _mock_session(monkeypatch, _Tx())
    monkeypatch.setattr("artmind.projection.keys_for_document", lambda tx, i: set())
    monkeypatch.setattr("artmind.projection.rebuild", lambda tx, keys, **kw: {})


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

    # vault file is gone from disk; the deletion is left for Obsidian Git to
    # commit (spec 2026-09-26, D1) -- artmind makes no commit of its own.
    assert not doc.exists()
    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "archive policy" not in log
    status = subprocess.run(["git", "status", "--porcelain"], cwd=vault, capture_output=True, text=True).stdout
    assert " D " in status or status.startswith(" D"), status
    assert "git_committed" not in result


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


def test_archive_document_reads_the_source_type_from_document_json(env, monkeypatch):
    """Spec 2026-09-26 R6: a converted binary's markdown carries identity
    only; its source type is in the staging folder's document.json."""
    vault, archive_root, kg_dir, originals = env

    doc = vault / "_derived" / "banking" / "deck.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("---\n_artmind_id: doc-2\n_domain: banking\n---\n\nBody.\n", encoding="utf-8")
    staged = kg_dir / "banking" / "deck"
    staged.mkdir(parents=True)
    (staged / "document.json").write_text(json.dumps({"id": "doc-2", "source_type": "pptx"}), encoding="utf-8")
    (originals / "deck.pptx").write_bytes(b"fake pptx bytes")

    monkeypatch.setattr(archive, "resolve_document_id", lambda name, domain: "doc-2")
    monkeypatch.setattr(
        archive, "_document_info",
        lambda doc_id: {"path": "_derived/banking/deck.md", "version": 1, "name": "deck.md"},
    )
    monkeypatch.setattr(archive, "_observation_valid_time_span", lambda doc_id: (None, None))
    _mock_session(monkeypatch, _Tx())
    monkeypatch.setattr("artmind.projection.keys_for_document", lambda tx, doc_id: set())
    monkeypatch.setattr("artmind.projection.rebuild", lambda tx, keys, **kw: {})

    result = archive.archive_document("banking", "deck")

    assert result["manifest"]["source_type"] == "pptx"
    assert (archive_root / "doc-2" / "original.pptx").read_bytes() == b"fake pptx bytes"


def test_archive_document_ignores_a_staged_document_json_naming_another_id(env, monkeypatch):
    """A folder at the note's stem whose document.json names another id is not
    this document's provenance: the legacy frontmatter (else "md") decides."""
    vault, archive_root, kg_dir, originals = env

    doc = vault / "_derived" / "banking" / "deck.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("---\n_artmind_id: doc-2\n_domain: banking\n_source_type: docx\n---\n\nBody.\n", encoding="utf-8")
    staged = kg_dir / "banking" / "deck"
    staged.mkdir(parents=True)
    (staged / "document.json").write_text(json.dumps({"id": "other", "source_type": "pptx"}), encoding="utf-8")

    monkeypatch.setattr(archive, "resolve_document_id", lambda name, domain: "doc-2")
    monkeypatch.setattr(
        archive, "_document_info",
        lambda doc_id: {"path": "_derived/banking/deck.md", "version": 1, "name": "deck.md"},
    )
    monkeypatch.setattr(archive, "_observation_valid_time_span", lambda doc_id: (None, None))
    _mock_session(monkeypatch, _Tx())
    monkeypatch.setattr("artmind.projection.keys_for_document", lambda tx, doc_id: set())
    monkeypatch.setattr("artmind.projection.rebuild", lambda tx, keys, **kw: {})

    result = archive.archive_document("banking", "deck")

    assert result["manifest"]["source_type"] == "docx"


def test_restore_under_a_new_id_rewrites_only_the_id_line(env, monkeypatch):
    vault, archive_root, kg_dir, originals = env
    bundle_dir = _seed_bundle(archive_root, "doc-1")
    (bundle_dir / "document.md").write_bytes(
        b"---\r\ntitle: Policy\r\n_artmind_id: doc-1\r\n_domain: general\r\n---\r\n\r\nBody.\r\n"
    )
    monkeypatch.setattr(archive, "_document_info", lambda doc_id: {})

    archive.restore_from_archive("doc-1", to_path="notes/policy-copy.md", new_id="doc-9")

    assert (vault / "notes" / "policy-copy.md").read_bytes() == (
        b"---\r\ntitle: Policy\r\n_artmind_id: doc-9\r\n_domain: general\r\n---\r\n\r\nBody.\r\n"
    )


def test_restore_under_a_new_id_stages_its_temp_file_in_the_vault_scratch_dir(env, monkeypatch):
    """A crash mid-write must leave an ignored scratch file, not an unignored
    one beside the note: the temp goes to the vault's .artmind/data."""
    from pathlib import Path

    import artmind.frontmatter as fm

    vault, archive_root, kg_dir, originals = env
    (vault / ".artmind").mkdir()
    _seed_bundle(archive_root, "doc-1")
    monkeypatch.setattr(archive, "_document_info", lambda doc_id: {})
    seen = []
    real_write = fm.write_note

    def spy(path, text, **kw):
        seen.append(kw.get("scratch_dir"))
        return real_write(path, text, **kw)

    monkeypatch.setattr(fm, "write_note", spy)

    archive.restore_from_archive("doc-1", to_path="notes/policy-copy.md", new_id="doc-9")

    assert [Path(s) for s in seen] == [vault / ".artmind" / "data"]
    assert "_artmind_id: doc-9" in (vault / "notes" / "policy-copy.md").read_text(encoding="utf-8")
    assert list((vault / "notes").glob("*.artmind-tmp")) == []


def test_restore_under_a_new_id_refuses_a_note_it_cannot_edit_line_by_line(env, monkeypatch):
    """A BOM note cannot be edited without touching more than the id line: a
    clear error, and nothing restored, staged or written."""
    vault, archive_root, kg_dir, originals = env
    bundle_dir = _seed_bundle(archive_root, "doc-1")
    (bundle_dir / "document.md").write_bytes(b"\xef\xbb\xbf---\n_artmind_id: doc-1\n---\n\nBody.\n")
    monkeypatch.setattr(archive, "_document_info", lambda doc_id: {})
    committed = []
    monkeypatch.setattr("artmind.ingest.commit_to_graph", lambda *a, **k: committed.append(a) or True)

    with pytest.raises(ValueError, match="byte-order mark"):
        archive.restore_from_archive("doc-1", to_path="notes/policy-copy.md", new_id="doc-9")

    assert not (vault / "notes" / "policy-copy.md").exists()
    assert committed == []
    # fresh target directory: not even the parent, a staging folder or a
    # registry row was created before the refusal
    assert not (vault / "notes").exists()
    assert list(kg_dir.rglob("*")) == []
    assert _registry_rows(kg_dir) == []


def test_archive_document_raises_when_not_found(env, monkeypatch):
    monkeypatch.setattr(archive, "resolve_document_id", lambda name, domain: None)
    with pytest.raises(ValueError, match="No document matching"):
        archive.archive_document("general", "nope")


@pytest.mark.parametrize("graph_path", ["notes/policy.md", "ABS"])
def test_archive_manifest_records_a_vault_relative_path(env, monkeypatch, graph_path):
    """The bundle travels between machines: `original_vault_path` is never a
    machine-absolute path -- including when the graph still holds a legacy
    absolute `Document.path`."""
    vault, archive_root, kg_dir, originals = env
    doc = vault / "notes" / "policy.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("---\n_artmind_id: doc-1\n---\n\nBody.\n", encoding="utf-8")
    _stub_archive_graph(monkeypatch, "doc-1", str(doc.resolve()) if graph_path == "ABS" else graph_path, "policy.md")

    archive.archive_document("general", "policy")

    manifest = json.loads((archive_root / "doc-1" / "manifest.json").read_text())
    assert manifest["original_vault_path"] == "notes/policy.md"


def test_restore_still_accepts_a_legacy_absolute_original_vault_path(env, monkeypatch):
    vault, archive_root, kg_dir, originals = env
    target = vault / "notes" / "policy.md"
    bundle_dir = _seed_bundle(archive_root, "doc-1", vault_path=str(target))
    monkeypatch.setattr(archive, "_document_info", lambda doc_id: {})
    monkeypatch.setattr("artmind.ingest.commit_to_graph", lambda *a, **k: True)
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda doc_id, domain=None: {})

    archive.restore_from_archive("doc-1")

    assert target.read_bytes() == (bundle_dir / "document.md").read_bytes()


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
    assert "committed" not in result
    assert retire_calls == [("doc-1", "general")]

    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "restore-from-archive" not in log


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


def test_restore_from_archive_swaps_the_kg_folder_in_atomically(env, monkeypatch):
    """Spec 2026-09-26 §14 A4: the restored folder -- with document.json
    already re-pointed at the new id -- lands in ONE swap that replaces
    whatever stale folder was there; a timer commit never sees a half-copied
    folder or the bundle's old identity."""
    from pathlib import Path

    vault, archive_root, kg_dir, originals = env
    bundle_dir = _seed_bundle(archive_root, "doc-1")
    doc_kg = bundle_dir / "kg" / "general" / "policy"
    (doc_kg / "chunks" / "sha").mkdir(parents=True)
    (doc_kg / "document.json").write_text(json.dumps({"id": "doc-1", "name": "policy.md"}), encoding="utf-8")
    (doc_kg / "observations.json").write_text("[]", encoding="utf-8")
    (doc_kg / "chunks" / "sha" / "0.json").write_text("cached", encoding="utf-8")
    stale = kg_dir / "general" / "policy-restored"
    stale.mkdir(parents=True)
    (stale / "stale.json").write_text("from an earlier life", encoding="utf-8")

    import artmind.atomic_dir as atomic_dir

    swaps = []
    real_swap = atomic_dir.write_dir_atomic

    def spy(target, files, **kw):
        swaps.append((Path(target), json.loads(files["document.json"]), kw))
        return real_swap(target, files, **kw)

    monkeypatch.setattr(atomic_dir, "write_dir_atomic", spy)
    monkeypatch.setattr(archive, "_document_info", lambda doc_id: {})
    monkeypatch.setattr("artmind.ingest.commit_to_graph", lambda *a, **k: True)
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda doc_id, domain=None: {})

    archive.restore_from_archive("doc-1", to_path="notes/policy-restored.md", new_id="doc-2")

    assert [(t, d["id"], kw) for t, d, kw in swaps] == [(stale, "doc-2", {"carry_over": False})]
    files = sorted(p.relative_to(stale).as_posix() for p in stale.rglob("*") if p.is_file())
    assert files == ["chunks/sha/0.json", "document.json", "observations.json"]
    assert json.loads((stale / "document.json").read_text(encoding="utf-8"))["id"] == "doc-2"
    assert sorted(p.name for p in (kg_dir / "general").iterdir()) == ["policy-restored"]


# ── review fixes: restore safety, atomicity, moved notes ─────────────────────


def _add_bundle_kg(bundle_dir, doc_id, document=None, *, stem="policy", raw=None):
    folder = bundle_dir / "kg" / "general" / stem
    folder.mkdir(parents=True)
    document = document or {"id": doc_id, "artmind_id": doc_id, "path": "/old/policy.md",
                            "name": "policy.md", "source_path": "notes/policy.md"}
    (folder / "document.json").write_text(raw if raw is not None else json.dumps(document), encoding="utf-8")
    (folder / "observations.json").write_text("[]", encoding="utf-8")
    return folder


def _stub_commit(monkeypatch):
    committed = []
    monkeypatch.setattr("artmind.ingest.commit_to_graph", lambda *a, **k: committed.append(a) or True)
    monkeypatch.setattr("artmind.lifecycle.retire_document", lambda doc_id, domain=None: {})
    monkeypatch.setattr(archive, "_document_info", lambda doc_id: {})
    return committed


def test_restore_under_a_new_path_repoints_the_staged_source_path_too(env, monkeypatch):
    from artmind.document_identity import canonical_path

    vault, archive_root, kg_dir, originals = env
    bundle_dir = _seed_bundle(archive_root, "doc-1")
    _add_bundle_kg(bundle_dir, "doc-1")
    _stub_commit(monkeypatch)

    archive.restore_from_archive("doc-1", to_path="n/q.md", new_id="doc-2")

    document = json.loads((kg_dir / "general" / "q" / "document.json").read_text(encoding="utf-8"))
    assert document["source_path"] == canonical_path(vault / "n" / "q.md") == "n/q.md"
    assert (document["id"], document["artmind_id"], document["name"]) == ("doc-2", "doc-2", "q.md")
    assert document["path"] == "n/q.md", "vault-relative: the staging folder is committed"


def test_restore_to_path_refuses_an_existing_different_note(env, monkeypatch):
    vault, archive_root, kg_dir, originals = env
    _seed_bundle(archive_root, "doc-1")
    target = vault / "n" / "q.md"
    target.parent.mkdir()
    target.write_text("---\n_artmind_id: other\n---\n\nMINE\n", encoding="utf-8")
    committed = _stub_commit(monkeypatch)

    with pytest.raises(ValueError, match="already holds a different file"):
        archive.restore_from_archive("doc-1", to_path="n/q.md")

    assert target.read_text(encoding="utf-8") == "---\n_artmind_id: other\n---\n\nMINE\n"
    assert committed == []


def test_restore_under_a_new_id_refuses_an_existing_note_when_the_bundle_has_no_document_md(env, monkeypatch):
    vault, archive_root, kg_dir, originals = env
    bundle_dir = _seed_bundle(archive_root, "doc-1")
    (bundle_dir / "document.md").unlink()
    target = vault / "n" / "q.md"
    target.parent.mkdir()
    target.write_text("---\n_artmind_id: other\n---\n\nMINE\n", encoding="utf-8")
    _stub_commit(monkeypatch)

    with pytest.raises(ValueError, match="already holds a different file"):
        archive.restore_from_archive("doc-1", to_path="n/q.md", new_id="doc-2")

    assert target.read_text(encoding="utf-8") == "---\n_artmind_id: other\n---\n\nMINE\n"


def test_restore_over_an_identical_existing_note_is_still_allowed(env, monkeypatch):
    vault, archive_root, kg_dir, originals = env
    bundle_dir = _seed_bundle(archive_root, "doc-1")
    target = vault / "n" / "q.md"
    target.parent.mkdir()
    target.write_bytes((bundle_dir / "document.md").read_bytes())
    _stub_commit(monkeypatch)

    result = archive.restore_from_archive("doc-1", to_path="n/q.md")

    assert result["restored_path"] == str(target)
    assert target.read_bytes() == (bundle_dir / "document.md").read_bytes()


def test_restore_with_a_malformed_document_json_writes_nothing(env, monkeypatch):
    """The bundle's document.json is parsed and re-pointed BEFORE the note is
    written: a bad one fails with no note, no parent directory, no staging
    folder and no registry row."""
    vault, archive_root, kg_dir, originals = env
    bundle_dir = _seed_bundle(archive_root, "doc-1")
    _add_bundle_kg(bundle_dir, "doc-1", raw="{not json")
    committed = _stub_commit(monkeypatch)

    with pytest.raises(ValueError, match="document.json"):
        archive.restore_from_archive("doc-1", to_path="brand/new/q.md", new_id="doc-2")

    assert not (vault / "brand").exists()
    assert list(kg_dir.rglob("*")) == []
    assert _registry_rows(kg_dir) == []
    assert committed == []


def test_archive_of_a_moved_note_still_bundles_its_staged_folder_and_original(env, monkeypatch):
    """The note moved (`git mv`) after extraction: its staging folder still has
    the old stem. The note is found by its id; the source type and original
    binary come from that folder, and the bundle restores under the new name."""
    vault, archive_root, kg_dir, originals = env
    note = vault / "_derived" / "banking" / "newname.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\n_artmind_id: doc-2\n_domain: banking\n---\n\nBody.\n", encoding="utf-8")
    staged = kg_dir / "banking" / "deck"
    staged.mkdir(parents=True)
    (staged / "document.json").write_text(json.dumps({"id": "doc-2", "source_type": "pptx"}), encoding="utf-8")
    (originals / "deck.pptx").write_bytes(b"fake pptx bytes")
    _stub_archive_graph(monkeypatch, "doc-2", "_derived/banking/newname.md", "newname.md")

    result = archive.archive_document("banking", "newname")

    bundle_dir = archive_root / "doc-2"
    assert result["manifest"]["source_type"] == "pptx"
    assert result["manifest"]["has_original_binary"] is True
    assert (bundle_dir / "original.pptx").read_bytes() == b"fake pptx bytes"
    assert json.loads((bundle_dir / "kg" / "banking" / "newname" / "document.json").read_text())["id"] == "doc-2"
    assert not (originals / "deck.pptx").exists()


def test_archive_prefers_the_staged_source_type_over_the_legacy_frontmatter_one(env, monkeypatch):
    vault, archive_root, kg_dir, originals = env
    note = vault / "_derived" / "banking" / "deck.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\n_artmind_id: doc-2\n_domain: banking\n_source_type: docx\n---\n\nBody.\n", encoding="utf-8")
    staged = kg_dir / "banking" / "deck"
    staged.mkdir(parents=True)
    (staged / "document.json").write_text(json.dumps({"id": "doc-2", "source_type": "pptx"}), encoding="utf-8")
    _stub_archive_graph(monkeypatch, "doc-2", "_derived/banking/deck.md", "deck.md")

    result = archive.archive_document("banking", "deck")

    assert result["manifest"]["source_type"] == "pptx"
