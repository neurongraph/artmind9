"""Document keys: same-named sources must not collide
(spec docs/superpowers/specs/2026-10-31-document-key-collisions-design.md)."""
import json
from hashlib import sha256

import click
import pytest

import artmind.ingest as ing
from conftest import _fake_docling, stage_as_extracted
from artmind.document_identity import KEY_HASH_CHARS, document_key


def test_key_is_stem_tilde_twelve_hex_of_the_path():
    path = "All_contracts/A/Summary.pdf"
    assert document_key(path) == "Summary~" + sha256(path.encode()).hexdigest()[:12]
    assert KEY_HASH_CHARS == 12


def test_same_name_in_two_folders_gets_two_keys():
    assert document_key("A/Summary.pdf") != document_key("B/Summary.pdf")


def test_canonical_path_is_vault_relative_through_a_symlinked_vault_dir(tmp_path, monkeypatch):
    import artmind.document_identity as di

    real = tmp_path / "real_vault"
    (real / "A").mkdir(parents=True)
    (real / "A" / "x.pdf").write_bytes(b"x")
    link = tmp_path / "link_vault"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", link)

    assert di.canonical_path(link / "A" / "x.pdf") == "A/x.pdf"
    assert di.canonical_path(real / "A" / "x.pdf") == "A/x.pdf"


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


import re
from pathlib import Path as _P

_HAND_BUILT = re.compile(
    r'MARKDOWNS_DIR\s*/\s*f"\{[^}]*\}(\.md|_artifacts|_chunks)"'
    r'|KG_DIR\s*/\s*\w+\s*/\s*[\w.]+\.stem\b'
)


def test_no_hand_built_derived_paths_outside_the_helpers():
    root = _P(ing.__file__).parent
    offenders = []
    for path in sorted(root.rglob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.strip()
            if code.startswith("#") or "`" in code:  # comments and docstring prose
                continue
            if _HAND_BUILT.search(line):
                offenders.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
    # The helpers' own one-line bodies, and `markdown_path_for`'s (whose
    # `stem` argument is now a document key).
    allowed = {"ingest.py": "return ", "document_identity.py": 'return MARKDOWNS_DIR / f"{stem}.md"'}
    offenders = [o for o in offenders if not (o.split(":")[0] in allowed and allowed[o.split(":")[0]] in o)]
    assert not offenders, "\n".join(offenders)


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


def test_retime_reads_a_keyed_binarys_markdown_from_its_recorded_path(ingest_env):
    from artmind.retime import staged_markdown_path

    keyed = ing._markdown_path("Summary~ab12cd34ef56")
    keyed.write_text("body", encoding="utf-8")
    doc = {"name": "Summary.pdf", "path": str(keyed)}
    assert staged_markdown_path(doc) == keyed
    assert staged_markdown_path({"name": "Summary.pdf"}) == ing._markdown_path("Summary")


# ── fix 1: an interrupted conversion is retried under its own name ───────────


def test_ownerless_keyed_markdown_is_this_sources_own(ingest_env):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    key = document_key("A/Summary.pdf")
    # What docling leaves when interrupted: the markdown, no sidecar.
    ing._markdown_path(key).write_text("# partial\n", encoding="utf-8")

    plan = ing.plan_binary(a, "general")

    assert plan.registered_path == ing._markdown_path(key)


def test_conversion_failure_after_claim_reconverts_under_the_same_key(ingest_env, monkeypatch):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    key = document_key("A/Summary.pdf")

    def failing(dest_path, image_model, **kwargs):
        # docling got as far as writing the markdown, then died
        ing._markdown_path(key).write_text("# partial\n", encoding="utf-8")
        return None, {"error": "interrupted"}

    monkeypatch.setattr(ing, "_convert_binary_via_docling", failing)
    r1 = ing.ingest_file(a, "m", "general", chunk_size=6000)
    assert r1["status"] == "failed"
    claim = json.loads((ing._chunks_dir(key) / ing.CONVERSION_SIDECAR).read_text())
    assert claim["source_path"] == "A/Summary.pdf"
    assert claim["source_sha256"] == ing._compute_sha256(a)

    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# A\n"]))
    r2 = ing.ingest_file(a, "m", "general", chunk_size=6000)

    assert r2["status"] == "ok"
    assert r2.get("tier") != "no_op"
    assert ing.Path(r2["registered_path"]).name == key + ".md"
    assert len(list(ing.MARKDOWNS_DIR.glob("Summary~*.md"))) == 1


# ── fix 2: a note's chunks follow its staging (legacy bare vs keyed) ────────


def test_legacy_note_keeps_bare_chunks_and_staging(ingest_env):
    vault, _ = ingest_env
    a = _note(vault, "A/README.md", "# A\n\nAlpha.")
    r1 = ing.ingest_file(a, "m", "general", chunk_size=6000)
    # Make it a note ingested before keys: bare staging holding its id, bare chunks.
    bare = ing._staging_dir("general", "README")
    bare.mkdir(parents=True)
    (bare / "document.json").write_text(json.dumps({"id": r1["artmind_id"]}))
    ing._chunks_dir("README").mkdir(parents=True)

    a.write_text(a.read_text().replace("Alpha.", "Alpha changed."))
    r2 = ing.ingest_file(a, "m", "general", chunk_size=6000)

    assert ing.Path(r2["chunks_dir"]) == ing._chunks_dir("README")
    assert list(ing._chunks_dir("README").glob("chunk_*.md"))
    assert ing._note_staging_dir("general", a, r2["artmind_id"]) == bare

    rebuilt = ing._build_file_result_from_db("README.md", "general")
    assert ing.Path(rebuilt["chunks_dir"]) == ing._chunks_dir("README")


# ── fix 3: a binary move is recorded ────────────────────────────────────────


def test_moved_binary_is_recorded_and_its_old_path_is_free(ingest_env, monkeypatch):
    vault, _ = ingest_env
    a = _vault_pdf(vault, "A/Summary.pdf", b"certificate A")
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# A\n", "# Other\n"]))
    r1 = ing.ingest_file(a, "m", "general", chunk_size=6000)
    folder = stage_as_extracted(ing.KG_DIR, r1)
    key = ing.Path(r1["registered_path"]).stem
    doc = json.loads((folder / "document.json").read_text())
    doc.update(path=r1["registered_path"], name=ing.Path(r1["registered_path"]).name)
    (folder / "document.json").write_text(json.dumps(doc))

    moved = vault / "C" / "Summary.pdf"
    moved.parent.mkdir()
    a.rename(moved)
    r2 = ing.ingest_file(moved, "m", "general", chunk_size=6000)

    assert r2["tier"] == "no_op"
    sidecar = json.loads((ing._chunks_dir(key) / ing.CONVERSION_SIDECAR).read_text())
    staged = json.loads((folder / "document.json").read_text())
    assert sidecar["source_path"] == staged["source_path"] == "C/Summary.pdf"
    assert sidecar["source_sha256"] == ing._compute_sha256(moved)
    assert (staged["path"], staged["name"]) == (doc["path"], doc["name"])  # the converted markdown's, untouched
    assert staged["id"] == r1["artmind_id"]

    # A different file now appears at the old path: its own document.
    other = _vault_pdf(vault, "A/Summary.pdf", b"a different certificate")
    r3 = ing.ingest_file(other, "m", "general", chunk_size=6000)

    assert r3["artmind_id"] != r1["artmind_id"]
    assert ing.Path(r3["registered_path"]).stem != key
    assert json.loads((folder / "document.json").read_text())["source_path"] == "C/Summary.pdf"


# ── fix 5: name lookups reach keyed documents ───────────────────────────────


def test_registry_miss_message_finds_a_keyed_staging_folder(ingest_env, monkeypatch):
    import paths
    from artmind.cli import _registry_miss_message

    monkeypatch.setattr(paths, "KG_DIR", ing.KG_DIR)

    keyed = ing._staging_dir("general", document_key("A/Summary.pdf"))
    keyed.mkdir(parents=True)
    (keyed / "document.json").write_text(json.dumps(
        {"id": "x", "name": "Summary~abc.md", "source_path": "A/Summary.pdf", "path": "A/Summary.pdf"}))
    other = ing._staging_dir("general", document_key("B/Summary.pdf"))
    other.mkdir(parents=True)
    (other / "document.json").write_text(json.dumps({"id": "y", "source_path": "B/Summary.pdf"}))

    message = _registry_miss_message("A/Summary.pdf", "general")
    assert "staged" in message and str(keyed) in message
    # a bare filename matches both: the first (sorted) is offered, not none
    assert "staged" in _registry_miss_message("Summary.pdf", "general")
    assert "staged" not in _registry_miss_message("Nothing.pdf", "general")


def _register_keyed(domain, *paths):
    for n, rel in enumerate(paths):
        md = ing._markdown_path(document_key(rel))
        ing.write_document(md, {"_artmind_id": f"id-{n}", "_domain": domain}, "# body\n")
        ing._register_document(domain, md, f"id-{n}", content_sha256=f"sha{n}")
    return [ing._markdown_path(document_key(rel)).name for rel in paths]


def test_prefix_matching_several_keyed_documents_is_ambiguous(ingest_env):
    names = _register_keyed("general", "A/Summary.pdf", "B/Summary.pdf")

    with pytest.raises(ing.AmbiguousDocumentName) as err:
        ing._build_file_result_from_db("Summary", "general")

    assert all(name in str(err.value) for name in names)
    # an exact name, and a unique prefix, behave as before
    assert ing._build_file_result_from_db(names[0], "general")["filename"] == names[0]
    assert ing._build_file_result_from_db("Summary~" + names[1].split("~")[1][:3], "general")["filename"] == names[1]


def test_ambiguous_name_is_a_click_error_in_the_cli(ingest_env):
    from artmind.cli import _registry_file_result

    _register_keyed("general", "A/Summary.pdf", "B/Summary.pdf")
    with pytest.raises(click.ClickException, match="matches 2 documents"):
        _registry_file_result("Summary", "general")


def test_chunk_counts_find_a_keyed_staging_folder(ingest_env, monkeypatch):
    from artmind import jobs

    monkeypatch.setattr(jobs, "KG_DIR", ing.KG_DIR)
    cache = ing._staging_dir("general", document_key("A/Summary.pdf")) / "chunks" / "sha1"
    cache.mkdir(parents=True)
    (cache / "chunk_001.json").write_text(json.dumps(
        {"entities": [{}, {}], "relationships": [{"rel_type": "X"}, {"rel_type": "EXTRACTED_FROM"}]}))

    # The caller only knows the filename's stem; the sha names the folder.
    assert jobs._chunk_counts("general", "Summary", "sha1", 1) == {"entities": 2, "relationships": 1}
    assert jobs._chunk_counts("general", "Summary", "other-sha", 1) is None


def test_new_note_chunks_and_staging_are_keyed(ingest_env):
    vault, _ = ingest_env
    a = _note(vault, "A/README.md", "# A\n\nAlpha.")
    r = ing.ingest_file(a, "m", "general", chunk_size=6000)
    key = document_key("A/README.md")

    assert ing.Path(r["chunks_dir"]) == ing._chunks_dir(key)
    assert ing._note_staging_dir("general", a, r["artmind_id"]) == ing._staging_dir("general", key)
    rebuilt = ing._build_file_result_from_db("README.md", "general")
    assert ing.Path(rebuilt["chunks_dir"]) == ing._chunks_dir(key)


def test_empty_id_note_staging_is_the_key_folder(ingest_env):
    vault, _ = ingest_env
    a = _note(vault, "A/README.md", "# A\n\nAlpha.")
    bare = ing._staging_dir("general", "README")
    bare.mkdir(parents=True)
    (bare / "document.json").write_text(json.dumps({"id": "some-id"}))
    assert ing._note_staging_dir("general", a, "") == ing._staging_dir("general", document_key("A/README.md"))
