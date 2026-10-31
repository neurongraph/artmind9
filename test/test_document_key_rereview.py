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
