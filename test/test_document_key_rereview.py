"""Re-review fixes for document keys (spec
docs/superpowers/specs/2026-10-31-document-key-collisions-design.md)."""
import json

import pytest

import artmind.ingest as ing
from conftest import stage_as_extracted
from artmind.document_identity import document_key


def _vault_pdf(vault, rel, data):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ── 1: the early claim must not clobber a good sidecar ──────────────────────


def test_failed_reconversion_leaves_the_previous_sidecar_alone(ingest_env, monkeypatch):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    s1 = ing._compute_sha256(a)

    def converted(dest_path, image_model, **kwargs):
        return "# A\n", {"conversion": {"render_version": 7}}

    monkeypatch.setattr(ing, "_convert_binary_via_docling", converted)
    r1 = ing.ingest_file(a, "m", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)
    key = ing.Path(r1["registered_path"]).stem
    sidecar_path = ing._chunks_dir(key) / ing.CONVERSION_SIDECAR
    before = json.loads(sidecar_path.read_text())
    assert before["source_sha256"] == s1 and before["conversion"] == {"render_version": 7}

    a.write_bytes(b"certificate A, edited")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", lambda *a, **k: (None, {"error": "docling died"}))
    r2 = ing.ingest_file(a, "m", "general", chunk_size=6000)

    assert r2["status"] == "failed"
    # the markdown and chunks are still the S1 body: so is the record of them
    assert json.loads(sidecar_path.read_text()) == before
    monkeypatch.setattr(ing, "_convert_binary_via_docling", converted)
    r3 = ing.ingest_file(a, "m", "general", chunk_size=6000)
    assert r3["status"] == "ok" and r3.get("tier") != "no_op"


# ── 2: sidecar writes are atomic ────────────────────────────────────────────


def test_sidecar_write_is_atomic_and_leaves_no_temp_file(ingest_env, monkeypatch):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    key = document_key("A/Summary.pdf")
    ing._claim_binary_key(key, ing._compute_sha256(a), a, "A/Summary.pdf")
    folder = ing._chunks_dir(key)
    assert json.loads((folder / ing.CONVERSION_SIDECAR).read_text())["source_path"] == "A/Summary.pdf"
    assert [p.name for p in folder.iterdir()] == [ing.CONVERSION_SIDECAR]

    # a write that dies before the swap keeps the old file whole, and cleans up
    monkeypatch.setattr(ing.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        ing._write_sidecar(folder, {"source_path": "elsewhere"})
    assert json.loads((folder / ing.CONVERSION_SIDECAR).read_text())["source_path"] == "A/Summary.pdf"
    assert [p.name for p in folder.iterdir()] == [ing.CONVERSION_SIDECAR]


# ── 3: exact-name ambiguity ─────────────────────────────────────────────────


def _register_notes(vault, *rels):
    for n, rel in enumerate(rels):
        note = vault / rel
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("# body\n", encoding="utf-8")
        ing._register_document("general", note, f"note-{n}", content_sha256=f"sha{n}")


def test_exact_name_in_two_folders_is_ambiguous_and_lists_paths(ingest_env):
    vault, _ = ingest_env
    _register_notes(vault, "A/README.md", "B/README.md")

    with pytest.raises(ing.AmbiguousDocumentName) as err:
        ing._build_file_result_from_db("README.md", "general")

    assert "A/README.md" in str(err.value) and "B/README.md" in str(err.value)
    # also with no domain filter
    with pytest.raises(ing.AmbiguousDocumentName):
        ing._build_file_result_from_db("readme.md", None)


def test_a_vault_relative_path_picks_one_of_two_namesakes(ingest_env):
    vault, _ = ingest_env
    _register_notes(vault, "A/README.md", "B/README.md")

    a = ing._build_file_result_from_db("A/README.md", "general")
    b = ing._build_file_result_from_db("B/README.md", "general")

    assert a["artmind_id"] == "note-0" and b["artmind_id"] == "note-1"


def test_retime_looks_the_document_up_by_its_id(ingest_env, monkeypatch):
    from artmind import retime

    vault, _ = ingest_env
    _register_notes(vault, "A/README.md", "B/README.md")
    seen = []
    monkeypatch.setattr(ing, "_build_file_result_from_db", lambda name, domain: seen.append(name))
    doc_dir = ing.KG_DIR / "general" / "README~x"
    doc_dir.mkdir(parents=True)
    (doc_dir / "document.json").write_text(json.dumps({"id": "note-1", "name": "README.md"}))

    out = retime._redo_properties(doc_dir, "general", {}, "m", "e")

    assert seen == ["B/README.md"] and "skipped" in out
