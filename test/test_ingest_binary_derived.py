"""Integration-level tests for artmind.ingest._ingest_binary_derived: the
orchestration of derived-markdown promotion (docs/document-identity.md,
"Derived-markdown promotion"; docs/redesign-phase-plan.md, Phase 5 "D")
against a real (temp) vault + git repo + registry, with docling itself
stubbed out (`_convert_binary_via_docling` is monkeypatched to return a
controllable body instead of shelling out to a real conversion).
"""
import subprocess

import pytest

import artmind.ingest as ing


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    _init_git_repo(vault)

    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", vault)
    import artmind.document_identity as di
    import artmind.vault_git as vg

    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", vault)
    monkeypatch.setattr(vg, "ARTMIND_VAULT_DIR", vault)

    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")

    originals = tmp_path / "data" / "originals"
    markdowns = tmp_path / "data" / "markdowns"
    originals.mkdir(parents=True)
    markdowns.mkdir(parents=True)
    monkeypatch.setattr(ing, "ORIGINALS_DIR", originals)
    monkeypatch.setattr(ing, "MARKDOWNS_DIR", markdowns)

    source = tmp_path / "incoming" / "deck.pptx"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"fake binary v1")

    return vault, source


def _fake_docling(body_by_call):
    """Return a `_convert_binary_via_docling` stand-in yielding successive
    bodies from `body_by_call` (a list), one per call."""
    calls = iter(body_by_call)

    def _convert(dest_path, image_model):
        return next(calls), {}

    return _convert


def test_first_ingest_converts_and_mints_an_artmind_id(env, monkeypatch):
    vault, source = env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))

    result = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "ok"
    assert result["version"] == 1
    assert "artmind_id" in result
    derived_path = vault / "_derived" / "general" / "deck.md"
    assert derived_path.exists()
    meta, body = ing._parse_md_frontmatter(derived_path.read_text())
    assert meta["_artmind_id"] == result["artmind_id"]
    assert meta["_source_type"] == "pptx"
    assert "_derived_sha256" in meta
    assert body == "# Deck\n\nBody v1.\n"

    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "convert" in log


def test_reingest_unchanged_binary_is_a_no_op(env, monkeypatch):
    vault, source = env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    # Re-ingest the identical binary bytes -- docling must not even be called.
    monkeypatch.setattr(
        ing, "_convert_binary_via_docling",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("docling should not run on a no_op")),
    )
    r2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["status"] == "ok"
    assert r2["tier"] == "no_op"
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"]
    assert "chunks_dir" not in r2


def test_binary_changed_reconverts_and_bumps_version(env, monkeypatch):
    vault, source = env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    source.write_bytes(b"fake binary v2 -- different bytes")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v2.\n"]))
    r2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["status"] == "ok"
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"] + 1
    derived_path = vault / "_derived" / "general" / "deck.md"
    _, body = ing._parse_md_frontmatter(derived_path.read_text())
    assert body == "# Deck\n\nBody v2.\n"


def test_editing_the_derived_markdown_promotes_it(env, monkeypatch):
    vault, source = env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    derived_path = vault / "_derived" / "general" / "deck.md"
    meta, _ = ing._parse_md_frontmatter(derived_path.read_text())
    from artmind.document_identity import render_document

    edited_body = "# Deck\n\nA human fixed this table.\n"
    derived_path.write_text(render_document(meta, edited_body))
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "hand edit"], cwd=vault, check=True)

    monkeypatch.setattr(
        ing, "_convert_binary_via_docling",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("docling should not run on a promote")),
    )
    result = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "ok"
    assert result.get("promoted") is True
    assert result["artmind_id"] == r1["artmind_id"]
    assert result["version"] == r1["version"] + 1
    promoted_path = vault / "general" / "deck.md"
    assert promoted_path.exists()
    assert not derived_path.exists()
    promoted_meta, promoted_body = ing._parse_md_frontmatter(promoted_path.read_text())
    assert promoted_meta["_source_type"] == "md"
    assert "_derived_sha256" not in promoted_meta
    assert promoted_body == edited_body

    # Reconverting the (unchanged) binary is now refused outright.
    result2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    assert result2["status"] == "failed"
    assert "already promoted" in result2["error"]


def test_binary_and_markdown_both_changed_is_a_collision(env, monkeypatch):
    vault, source = env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    derived_path = vault / "_derived" / "general" / "deck.md"
    meta, _ = ing._parse_md_frontmatter(derived_path.read_text())
    from artmind.document_identity import render_document

    derived_path.write_text(render_document(meta, "# Deck\n\nA human fixed this table.\n"))
    subprocess.run(["git", "add", "-A"], cwd=vault, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "hand edit"], cwd=vault, check=True)

    source.write_bytes(b"fake binary v2 -- different bytes")
    monkeypatch.setattr(
        ing, "_convert_binary_via_docling",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("docling should not run on a collision")),
    )
    result = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "failed"
    assert "both the original binary and its derived markdown" in result["error"]
    # Nothing was touched -- still sitting exactly as the hand-edit left it.
    assert derived_path.exists()
