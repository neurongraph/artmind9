"""Integration-level tests for artmind.ingest._ingest_vault_native: the
orchestration of resolve_identity + decide_version + build_frontmatter +
_register_document against a real (temp) vault and registry, with only
Neo4j/LLM stubbed out. This is exactly the seam where a path-representation
mismatch between registration and resolution hid (registering with a raw
`.resolve()` while resolution looked up with `canonical_path()`) — a purely
unit-level test of either side in isolation would not have caught it.
"""
import subprocess

import pytest

import artmind.ingest as ing


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    (v / "notes").mkdir(parents=True)
    doc = v / "notes" / "doc.md"
    doc.write_text("# Doc\n\nOriginal body.\n", encoding="utf-8")
    _init_git_repo(v)

    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", v)
    import artmind.document_identity as di

    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", v)

    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")

    # No real Neo4j: the metadata-only fast path's graph update is a no-op.
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: None)

    return v, doc


def test_first_ingest_is_new_and_writes_full_system_block(vault):
    v, doc = vault
    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "ok"
    assert result["resolution_verdict"] == "new"
    assert result["version"] == 1
    assert result["tier"] == "content"

    meta, _ = ing._parse_md_frontmatter(doc.read_text())
    assert meta["_artmind_id"] == result["artmind_id"]
    assert meta["_domain"] == "general"
    assert meta["_status"] == "latest"


def test_reingest_same_path_unchanged_body_is_metadata_only(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert r2["resolution_verdict"] == "reingest"
    assert r2["tier"] == "metadata_only"
    assert r2["version"] == r1["version"]
    assert r2["artmind_id"] == r1["artmind_id"]


def test_reingest_edited_body_bumps_version(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    doc.write_text(doc.read_text() + "\nA new paragraph.\n")
    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["resolution_verdict"] == "reingest"
    assert r2["tier"] == "content"
    assert r2["version"] == r1["version"] + 1
    assert r2["artmind_id"] == r1["artmind_id"]


def test_git_mv_is_recognised_as_a_silent_move(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    new_path = v / "notes" / "renamed.md"
    subprocess.run(["git", "mv", "notes/doc.md", "notes/renamed.md"], cwd=v, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "rename"], cwd=v, check=True)

    r2 = ing.ingest_file(new_path, "gemma4:e4b", "general", chunk_size=6000)
    assert r2["resolution_verdict"] == "move"
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"]


def test_copy_with_same_frontmatter_id_refuses(vault):
    """Two live files sharing one id -- the old path never went away."""
    v, doc = vault
    ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    copy_path = v / "notes" / "copy.md"
    copy_path.write_text(doc.read_text())  # literal copy, same _artmind_id
    subprocess.run(["git", "add", "-A"], cwd=v, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "copy"], cwd=v, check=True)

    result = ing.ingest_file(copy_path, "gemma4:e4b", "general", chunk_size=6000)
    assert result["status"] == "failed"
    assert "already registered" in result["error"]


def test_copy_with_fork_mints_independent_identity(vault):
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    copy_path = v / "notes" / "copy.md"
    copy_path.write_text(doc.read_text())
    subprocess.run(["git", "add", "-A"], cwd=v, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "copy"], cwd=v, check=True)

    r2 = ing.ingest_file(copy_path, "gemma4:e4b", "general", chunk_size=6000, fork=True)
    assert r2["status"] == "ok"
    assert r2["resolution_verdict"] == "new"
    assert r2["artmind_id"] != r1["artmind_id"]


def test_frontmatter_domain_wins_over_domain_argument(vault):
    v, doc = vault
    doc.write_text("---\n_domain: technical_paper\n---\n\n# Doc\n\nBody.\n")

    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert result["domain"] == "technical_paper"


def test_set_domain_overrides_frontmatter_and_forces_content_tier(vault):
    v, doc = vault
    ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)  # v1, domain=general
    ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)  # metadata_only, unchanged

    result = ing.ingest_file(
        doc, "gemma4:e4b", "general", chunk_size=6000, set_domain="technical_paper",
    )
    assert result["domain"] == "technical_paper"
    assert result["tier"] == "content"  # forced re-extraction despite unchanged body


def test_missing_domain_fails_clearly(vault):
    v, doc = vault
    result = ing.ingest_file(doc, "gemma4:e4b", None, chunk_size=6000)
    assert result["status"] == "failed"
    assert "_domain" in result["error"]


def test_touched_path_is_set_for_git_batching(vault):
    v, doc = vault
    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert result["touched_path"] == doc
