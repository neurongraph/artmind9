"""Document keys: same-named sources must not collide
(spec docs/superpowers/specs/2026-10-31-document-key-collisions-design.md)."""
import json
from hashlib import sha256

import artmind.ingest as ing
from conftest import _fake_docling, stage_as_extracted
from artmind.document_identity import KEY_HASH_CHARS, document_key


def test_key_is_stem_tilde_twelve_hex_of_the_path():
    path = "All_contracts/A/Summary.pdf"
    assert document_key(path) == "Summary~" + sha256(path.encode()).hexdigest()[:12]
    assert KEY_HASH_CHARS == 12


def test_same_name_in_two_folders_gets_two_keys():
    assert document_key("A/Summary.pdf") != document_key("B/Summary.pdf")


def test_extension_is_part_of_the_key():
    assert document_key("A/X.pdf") != document_key("A/X.docx")


def test_key_depends_on_the_path_alone():
    # Two laptops, no registry: the same vault path gives the same name.
    assert document_key("A/Summary.pdf") == document_key("A/Summary.pdf")


def test_full_key_carries_the_whole_digest():
    path = "A/Summary.pdf"
    assert document_key(path, full=True) == "Summary~" + sha256(path.encode()).hexdigest()


def test_path_helpers_name_by_key(ingest_env):
    assert ing._markdown_path("S~ab") == ing.MARKDOWNS_DIR / "S~ab.md"
    assert ing._artifacts_dir("S~ab") == ing.MARKDOWNS_DIR / "S~ab_artifacts"
    assert ing._chunks_dir("S~ab") == ing.MARKDOWNS_DIR / "S~ab_chunks"
    assert ing._staging_dir("d", "S~ab") == ing.KG_DIR / "d" / "S~ab"


def test_converted_owner_reads_the_conversion_sidecar(ingest_env):
    chunks = ing._chunks_dir("Summary")
    chunks.mkdir(parents=True)
    (chunks / ing.CONVERSION_SIDECAR).write_text(json.dumps(
        {"source_path": "A/Summary.pdf", "source_sha256": "abc", "source_type": "pdf"}))
    assert ing._converted_owner("Summary", "general") == ("A/Summary.pdf", "abc")


def test_converted_owner_falls_back_to_staging(ingest_env):
    folder = ing._staging_dir("general", "Summary")
    folder.mkdir(parents=True)
    (folder / "document.json").write_text(json.dumps(
        {"id": "x", "source_path": "B/Summary.pdf", "source_sha256": "def"}))
    assert ing._converted_owner("Summary", "general") == ("B/Summary.pdf", "def")


def test_converted_owner_is_none_for_a_free_name(ingest_env):
    assert ing._converted_owner("Nothing", "general") is None


def _vault_pdf(vault, rel, data):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_two_same_named_pdfs_become_two_documents(ingest_env, monkeypatch):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    b = _vault_pdf(vault, "B/Summary.pdf", b"certificate B")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# A\n", "# B\n"]))

    ra = ing.ingest_file(a, "m", "general", chunk_size=6000)
    rb = ing.ingest_file(b, "m", "general", chunk_size=6000)

    assert ra["artmind_id"] != rb["artmind_id"]
    assert ra["version"] == rb["version"] == 1
    assert ra["registered_path"] != rb["registered_path"]
    assert ing.Path(ra["registered_path"]).name == document_key("A/Summary.pdf") + ".md"
    assert ing.Path(rb["chunks_dir"]).name == document_key("B/Summary.pdf") + "_chunks"


def test_existing_bare_stem_document_keeps_its_name(ingest_env, monkeypatch):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    ing.write_document(ing._markdown_path("Summary"), {"_artmind_id": "old-id", "_domain": "general"}, "# A\n")
    chunks = ing._chunks_dir("Summary")
    chunks.mkdir(parents=True)
    (chunks / ing.CONVERSION_SIDECAR).write_text(json.dumps(
        {"source_path": "A/Summary.pdf", "source_sha256": ing._compute_sha256(a), "source_type": "pdf"}))

    plan = ing.plan_binary(a, "general")

    assert plan.registered_path == ing._markdown_path("Summary")
    assert plan.existing_meta["_artmind_id"] == "old-id"


def test_namesake_of_a_bare_stem_document_gets_its_own_key(ingest_env, monkeypatch):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    b = _vault_pdf(vault, "B/Summary.pdf", b"certificate B")
    ing.write_document(ing._markdown_path("Summary"), {"_artmind_id": "old-id", "_domain": "general"}, "# A\n")
    chunks = ing._chunks_dir("Summary")
    chunks.mkdir(parents=True)
    (chunks / ing.CONVERSION_SIDECAR).write_text(json.dumps(
        {"source_path": "A/Summary.pdf", "source_sha256": ing._compute_sha256(a), "source_type": "pdf"}))

    plan = ing.plan_binary(b, "general")

    assert plan.registered_path == ing._markdown_path(document_key("B/Summary.pdf"))
    assert plan.is_new


def test_moved_binary_keeps_its_key(ingest_env, monkeypatch):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# A\n"]))
    r1 = ing.ingest_file(a, "m", "general", chunk_size=6000)
    stage_as_extracted(ing.KG_DIR, r1)

    moved = vault / "C" / "Summary.pdf"
    moved.parent.mkdir()
    a.rename(moved)
    plan = ing.plan_binary(moved, "general")

    assert plan.registered_path == ing.Path(r1["registered_path"])
    assert plan.unchanged


def test_collision_guard_never_takes_another_sources_key(ingest_env, monkeypatch):
    vault, _ = ingest_env
    b = _vault_pdf(vault, "B/Summary.pdf", b"certificate B")
    _vault_pdf(vault, "Z/Other.pdf", b"other")
    taken = document_key("B/Summary.pdf")
    chunks = ing._chunks_dir(taken)
    chunks.mkdir(parents=True)
    (chunks / ing.CONVERSION_SIDECAR).write_text(json.dumps(
        {"source_path": "Z/Other.pdf", "source_sha256": "zzz", "source_type": "pdf"}))

    plan = ing.plan_binary(b, "general")

    assert plan.registered_path == ing._markdown_path(document_key("B/Summary.pdf", full=True))


def test_docling_writes_the_keyed_markdown_and_artifacts(ingest_env, monkeypatch, tmp_path):
    vault, _ = ingest_env
    monkeypatch.setattr(ing, "LOGS_DIR", tmp_path / "logs")  # the scratch link goes here
    (tmp_path / "logs").mkdir()
    a = _vault_pdf(vault, "A/Summary.pdf", b"%PDF certificate A")
    key = document_key("A/Summary.pdf")
    seen = {}

    def fake_run(command, cwd=None):
        seen["command"] = command
        (ing.MARKDOWNS_DIR / f"{key}.md").write_text("# A\n")
        return 0, "", ""

    monkeypatch.setattr(ing, "run_command", fake_run)
    monkeypatch.setattr(ing, "_render_from_docling_json", lambda *a, **k: None)

    body, _ = ing._convert_binary_via_docling(a.resolve(), "m", key=key)

    assert body.strip() == "# A"
    assert f'{key}.pdf"' in seen["command"]
    assert not list(ing.MARKDOWNS_DIR.glob("Summary.md"))


def _note(vault, rel, body):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n_domain: general\n---\n{body}\n")
    return path


def test_two_same_named_notes_get_distinct_staging(ingest_env):
    vault, _ = ingest_env
    a = _note(vault, "A/README.md", "# A\n\nAlpha.")
    b = _note(vault, "B/README.md", "# B\n\nBeta.")
    ra = ing.ingest_file(a, "m", "general", chunk_size=6000)
    rb = ing.ingest_file(b, "m", "general", chunk_size=6000)

    assert ra["chunks_dir"] != rb["chunks_dir"]
    da = ing._note_staging_dir("general", ing.Path(ra["registered_path"]), ra["artmind_id"])
    db_ = ing._note_staging_dir("general", ing.Path(rb["registered_path"]), rb["artmind_id"])
    assert da != db_


def test_existing_note_keeps_its_bare_staging_folder(ingest_env):
    vault, _ = ingest_env
    a = _note(vault, "A/README.md", "# A\n\nAlpha.")
    ra = ing.ingest_file(a, "m", "general", chunk_size=6000)
    bare = ing._staging_dir("general", "README")
    bare.mkdir(parents=True)
    (bare / "document.json").write_text(json.dumps({"id": ra["artmind_id"]}))

    assert ing._note_staging_dir("general", a, ra["artmind_id"]) == bare


def test_new_note_staging_is_keyed(ingest_env):
    vault, _ = ingest_env
    a = _note(vault, "A/README.md", "# A\n\nAlpha.")
    ra = ing.ingest_file(a, "m", "general", chunk_size=6000)
    assert ing._note_staging_dir("general", a, ra["artmind_id"]) == ing._staging_dir(
        "general", document_key("A/README.md"))
