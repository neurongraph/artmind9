"""Integration-level tests for artmind.ingest._ingest_binary_derived:
converting a binary source (pdf/pptx/docx) to markdown and storing it
permanently at `.artmind`'s own markdown store (docs/vault.md, "The
ownership rule"), against a real (temp) vault + git repo + registry, with
docling itself stubbed out (`_convert_binary_via_docling` is monkeypatched to
return a controllable body instead of shelling out to a real conversion).

Covers the one decision left after promotion was deleted -- no_op vs
convert, driven by comparing the incoming binary's hash against the
`source_sha256` its last extraction staged in document.json (spec 2026-09-26
R6; the converted markdown's own `_source_sha256` frontmatter is the legacy
fallback) -- for both a source copied in from outside the vault and a
vault-resident source.
"""
import json
import subprocess

import artmind.ingest as ing
from conftest import _fake_docling, stage_as_extracted


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
    assert meta == {"_artmind_id": result["artmind_id"], "_domain": "general"}
    assert result["provenance"]["source_type"] == "pptx"
    assert result["provenance"]["source_sha256"] == ing._compute_sha256(source)
    assert body == "# Deck\n\nBody v1.\n"

    # artmind never commits (spec 2026-09-26 D1): the derived markdown is left
    # for Obsidian Git to commit, so history still holds only the fixture's seed.
    log = subprocess.run(["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True).stdout
    assert "convert" not in log
    status = subprocess.run(["git", "status", "--porcelain"], cwd=vault, capture_output=True, text=True).stdout
    assert status.strip(), "the derived markdown is on disk, uncommitted"


def test_reingest_unchanged_binary_is_a_no_op(ingest_env, monkeypatch):
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    r1 = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)

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
    stage_as_extracted(ing.KG_DIR, r1)

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
    stage_as_extracted(ing.KG_DIR, r1)

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


def test_an_unextracted_conversion_is_not_a_no_op(ingest_env, monkeypatch):
    """The source hash is recorded by the extraction (document.json), not at
    conversion: a conversion whose extraction never ran converts again
    rather than no-op forever on a markdown nothing was extracted from."""
    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"] * 2))

    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)  # extraction then fails
    again = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert again.get("tier") != "no_op"
    assert "chunks_dir" in again, "converted and chunked again, ready to extract"
    assert again["version"] == 1


def test_a_legacy_conversion_no_ops_from_its_own_frontmatter(ingest_env, monkeypatch):
    """Backward compatibility: a conversion stamped by pre-R6 artmind (fat
    frontmatter, a document.json without `source_sha256`) still no-ops."""
    vault, source = ingest_env
    derived = ing.MARKDOWNS_DIR / "deck.md"
    derived.write_text(
        "---\n_artmind_id: 0192legacy\n_version: 3\n_domain: general\n_source_sha256: "
        + ing._compute_sha256(source) + "\n---\n\n# Deck\n"
    )
    folder = ing.KG_DIR / "general" / "deck"
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps({"id": "0192legacy", "version": 3}))
    monkeypatch.setattr(
        ing, "_convert_binary_via_docling",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("docling should not run on a no_op")),
    )

    result = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert (result["tier"], result["version"], result["artmind_id"]) == ("no_op", 3, "0192legacy")
