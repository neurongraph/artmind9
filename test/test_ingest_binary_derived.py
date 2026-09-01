"""Integration-level tests for artmind.ingest._ingest_binary_derived: the
orchestration of derived-markdown promotion (docs/document-identity.md,
"Derived-markdown promotion"; docs/redesign-phase-plan.md, Phase 5 "D")
against a real (temp) vault + git repo + registry, with docling itself
stubbed out (`_convert_binary_via_docling` is monkeypatched to return a
controllable body instead of shelling out to a real conversion).
"""
import subprocess

import artmind.ingest as ing
from conftest import _fake_docling


def test_first_ingest_converts_and_mints_an_artmind_id(ingest_env, monkeypatch):
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))

    result = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "ok"
    assert result["version"] == 1
    assert "artmind_id" in result
    derived_path = ing.MARKDOWNS_DIR / "deck.md"
    assert derived_path.exists()
    meta, body = ing._parse_md_frontmatter(derived_path.read_text())
    assert meta["_artmind_id"] == result["artmind_id"]
    assert meta["_source_type"] == "pptx"
    assert "_source_sha256" in meta
    assert body == "# Deck\n\nBody v1.\n"

    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "convert" in log


def test_reingest_unchanged_binary_is_a_no_op(ingest_env, monkeypatch):
    vault, source = ingest_env
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


def test_binary_changed_reconverts_and_bumps_version(ingest_env, monkeypatch):
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    source.write_bytes(b"fake binary v2 -- different bytes")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v2.\n"]))
    r2 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["status"] == "ok"
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"] + 1
    derived_path = ing.MARKDOWNS_DIR / "deck.md"
    _, body = ing._parse_md_frontmatter(derived_path.read_text())
    assert body == "# Deck\n\nBody v2.\n"


def test_the_converted_markdown_stays_in_artmind(ingest_env, monkeypatch):
    """No `_derived/` in the vault: `.artmind/` owns the conversion, so it has
    one location for its whole life and nothing ever moves."""
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody.\n"]))

    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert not (vault / "_derived").exists()
    assert (ing.MARKDOWNS_DIR / "deck.md").is_file()


def test_a_vault_resident_binary_no_ops_on_an_unchanged_reingest(ingest_env, monkeypatch):
    """This is exactly the correctness gap `_source_sha256` was introduced to
    close: Task 5 left a vault-resident binary unable to tell if it changed
    (no separate persisted copy to diff bytes against), so it always
    reconverted, even byte-for-byte unchanged. Comparing the incoming
    binary's own hash against `_source_sha256` in the registered markdown's
    frontmatter fixes that regardless of where the source lives.
    """
    vault, _source = ingest_env
    resident = vault / "area1" / "deck.pptx"
    resident.parent.mkdir(parents=True)
    resident.write_bytes(b"fake binary v1")

    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    r1 = ing.ingest_file(resident, "gemma4:e4b", "general", chunk_size=6000)
    assert r1["status"] == "ok"

    monkeypatch.setattr(
        ing, "_convert_binary_via_docling",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("docling should not run on a no_op")),
    )
    r2 = ing.ingest_file(resident, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["status"] == "ok"
    assert r2["tier"] == "no_op"
    assert r2["artmind_id"] == r1["artmind_id"]
    assert r2["version"] == r1["version"]
    assert "chunks_dir" not in r2
