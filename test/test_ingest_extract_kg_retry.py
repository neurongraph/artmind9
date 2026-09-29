"""Regression for the `ingest extract-kg` CLI retry path.

Repro: `artmind ingest sync FILE.md --domain DOMAIN` on a vault-native
markdown file, then `artmind ingest extract-kg FILE.md --domain DOMAIN` (e.g.
after the first extraction failed / produced 0 entities). That command builds
its `file_result` via `_build_file_result_from_db` rather than
`_ingest_vault_native`'s own return value -- and `extract_kg` (ingest.py)
branches on `"artmind_id" in file_result` to identify a vault-native doc, then
reads `file_result["version"]` unconditionally in that branch. Until this fix,
`_build_file_result_from_db` populated `artmind_id` from the registry but
never `version` at all -- the registry's `documents` table has no version
column (docs/redesign-phase-plan.md, "E"); only the frontmatter does -- so
this retry path crashed with `KeyError: 'version'` for every vault-native
document.
"""
import json
import subprocess

import artmind.db as db
import artmind.ingest as ing
from conftest import stage_as_extracted


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=path, check=True)


def _ingest_vault_doc(tmp_path, monkeypatch, filename="retry_doc.md", body="Original body."):
    """Ingest one vault-native markdown file for real (frontmatter + registry),
    mirroring `artmind ingest sync` -- the same setup test_ingest_vault_native.py
    uses for `_ingest_vault_native` itself."""
    v = tmp_path / "vault"
    (v / "notes").mkdir(parents=True, exist_ok=True)
    doc = v / "notes" / filename
    doc.write_text(f"# Doc\n\n{body}\n", encoding="utf-8")
    _init_git_repo(v)

    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", v)
    import artmind.document_identity as di

    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", v)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: None)
    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "kg")

    result = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert result["status"] == "ok"
    return v, doc, result


def test_build_file_result_from_db_populates_version_for_vault_native_doc(tmp_path, monkeypatch):
    _v, doc, first = _ingest_vault_doc(tmp_path, monkeypatch)

    rebuilt = ing._build_file_result_from_db("retry_doc.md", "general")

    assert rebuilt is not None
    assert rebuilt["artmind_id"] == first["artmind_id"]
    assert "version" in rebuilt, (
        "extract_kg reads file_result['version'] unconditionally whenever "
        "'artmind_id' in file_result -- see ingest.py's extract_kg identity block"
    )
    assert rebuilt["version"] == first["version"] == 1
    # The registry stores a vault-relative path (document_identity.canonical_path);
    # every other producer of file_result["registered_path"] hands back an
    # absolute one, and extract_kg reads frontmatter/chunks off of it directly --
    # a bare relative string only works by accident of the caller's cwd.
    assert rebuilt["registered_path"] == str(doc)


def test_build_file_result_from_db_reflects_a_later_version_bump(tmp_path, monkeypatch):
    """The retry lands on the version the failed run chose: the extraction
    that failed staged nothing, so the baseline is still version 1's."""
    _v, doc, first = _ingest_vault_doc(tmp_path, monkeypatch)
    stage_as_extracted(ing.KG_DIR, first)

    doc.write_text(doc.read_text() + "\nA new paragraph.\n")
    second = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert second["version"] == first["version"] + 1

    rebuilt = ing._build_file_result_from_db("retry_doc.md", "general")
    assert rebuilt["version"] == second["version"] == 2
    assert rebuilt["provenance"]["content_sha256"] == second["content_sha256"]
    assert rebuilt["provenance"]["source_path"] == "notes/retry_doc.md"


def test_build_file_result_from_db_defaults_to_one_when_frontmatter_unreadable(tmp_path, monkeypatch):
    """The registered path having gone missing (or lost its frontmatter)
    between sync and the extract-kg retry must not crash the lookup --
    version falls back to 1, same as the binary no_op path's own fallback."""
    _v, doc, first = _ingest_vault_doc(tmp_path, monkeypatch)

    result = ing._build_file_result_from_db("retry_doc.md", "general")
    assert result["registered_path"] == str(doc)
    doc.unlink()

    rebuilt = ing._build_file_result_from_db("retry_doc.md", "general")
    assert rebuilt["artmind_id"] == first["artmind_id"]
    assert rebuilt["version"] == 1


def test_extract_kg_does_not_crash_on_the_cli_retry_file_result(tmp_path, monkeypatch):
    """End-to-end repro: sync, then run extract-kg's retry path against it."""
    _v, _doc, _first = _ingest_vault_doc(tmp_path, monkeypatch)

    monkeypatch.setattr(ing, "KG_DIR", tmp_path / "kg")
    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])
    monkeypatch.setattr(ing, "build_entities_prompt", lambda text, schema, vocabulary=None: "p")
    monkeypatch.setattr(ing, "build_properties_prompt", lambda text, ents, schema, vocabulary=None: "p")
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda text, ents, schema: "p")
    monkeypatch.setattr(ing, "_llm_extract", lambda step_name, model, prompt, debug_dir: ([], True))

    file_result = ing._build_file_result_from_db("retry_doc.md", "general")
    assert file_result is not None

    doc_kg_dir = ing.extract_kg(file_result, "general", max_workers=1)

    assert doc_kg_dir is not None
    # Spec 2026-09-26 R6: the extraction records the ingest's provenance in
    # document.json -- the next ingest's baseline -- and seeds the title the
    # note no longer carries.
    document = json.loads((doc_kg_dir / "document.json").read_text())
    assert document["id"] == file_result["artmind_id"]
    assert document["content_sha256"] == file_result["provenance"]["content_sha256"]
    assert {"source_commit", "ingested_at", "source_path", "source_type"} <= set(document)
    assert document["title"] == "retry_doc"


# ── domain=None: the job-chunks / admin-console progress lookup ─────────────
# Found live: admin-ui's per-file chunk-progress view showed "Failed to load
# chunks: ... not found in registry for domain 'general'" for every file in a
# manifest-driven batch. Root cause: cli.py's `ingest job-chunks` and
# dashboard_routes.py's `/api/jobs/{id}/chunks` both filtered the registry
# lookup on the JOB's own stored domain -- but a directory `ingest async`
# resolves each file's domain individually from vault.yaml (the
# ingest-async-domain-mapping fix), so the job's own domain is only ever a
# fallback and rarely names the domain any given file actually landed under.


def test_build_file_result_from_db_finds_a_doc_registered_under_a_different_domain(
    tmp_path, monkeypatch
):
    """The exact repro: the document is registered under 'banking.cases', but
    the caller (job-chunks) only has the job's own domain, 'general', in hand."""
    _v, _doc, _first = _ingest_vault_doc(
        tmp_path, monkeypatch, filename="cases_doc.md"
    )
    # Re-register it under a different domain, simulating a manifest-mapped
    # file that never was 'general' to begin with.
    conn = db._get_db()
    conn.execute("UPDATE documents SET domain = 'banking.cases'")
    conn.commit()
    conn.close()

    assert ing._build_file_result_from_db("cases_doc.md", "general") is None, (
        "sanity check: the old domain-scoped lookup really does miss it"
    )

    rebuilt = ing._build_file_result_from_db("cases_doc.md", None)

    assert rebuilt is not None
    assert rebuilt["filename"] == "cases_doc.md"


# ── a binary whose first extraction failed ──────────────────────────────────
# `_build_file_result_from_db` rebuilds the ingest's provenance from the
# registry, whose row points at the DERIVED markdown -- not at the binary it
# was converted from. Claiming `md` there (or leaving `source_sha256` out)
# made the next `ingest sync` of the unchanged binary convert it again.


def _stub_extraction(monkeypatch):
    monkeypatch.setattr(ing, "_embed_text", lambda model, text: [0.0, 0.1])
    monkeypatch.setattr(ing, "build_entities_prompt", lambda *a, **k: "p")
    monkeypatch.setattr(ing, "build_properties_prompt", lambda *a, **k: "p")
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda *a, **k: "p")
    monkeypatch.setattr(ing, "_llm_extract", lambda *a, **k: ([], True))


def _no_docling(*a, **k):
    raise AssertionError("docling should not run on a no_op")


def test_a_binarys_extract_kg_retry_keeps_its_source_provenance(ingest_env, monkeypatch):
    from conftest import _fake_docling

    vault, source = ingest_env
    _stub_extraction(monkeypatch)
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    first = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    original = first["provenance"]  # what the extraction that failed would have recorded

    rebuilt = ing._build_file_result_from_db("deck.md", "general")
    folder = ing.extract_kg(rebuilt, "general", max_workers=1)

    document = json.loads((folder / "document.json").read_text())
    assert document["source_sha256"] == ing._compute_sha256(source) == original["source_sha256"]
    assert document["source_type"] == "pptx"
    assert document["source_path"] == original["source_path"]
    assert document["source_path"].endswith("/deck.pptx") and "_external_docs/" in document["source_path"]

    monkeypatch.setattr(ing, "_convert_binary_via_docling", _no_docling)
    again = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    assert (again["tier"], again["version"]) == ("no_op", 1), "the unchanged binary is not converted again"


def test_a_retry_records_the_conversion_that_failed_not_the_previous_binarys(ingest_env, monkeypatch):
    """Version 1 extracted; the binary changes; its conversion's extraction
    fails; the retry must record the NEW binary's hash, not v1's."""
    from conftest import _fake_docling

    vault, source = ingest_env
    _stub_extraction(monkeypatch)
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n", "# Deck\n\nBody v2.\n"]))
    first = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    ing.extract_kg(first, "general", max_workers=1)
    source.write_bytes(b"fake binary v2")
    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)  # extraction then fails

    folder = ing.extract_kg(ing._build_file_result_from_db("deck.md", "general"), "general", max_workers=1)

    document = json.loads((folder / "document.json").read_text())
    assert document["version"] == 2
    assert document["source_sha256"] == ing._compute_sha256(source) != first["provenance"]["source_sha256"]


def test_a_retry_that_cannot_find_the_binary_does_not_claim_md(ingest_env, monkeypatch):
    """A derived markdown with no record of its binary (a conversion from
    before this was kept): no source_sha256, and never `source_type: md`."""
    from conftest import _fake_docling

    vault, source = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))
    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)
    (ing.MARKDOWNS_DIR / "deck_chunks" / "conversion.json").unlink()

    rebuilt = ing._build_file_result_from_db("deck.md", "general")

    assert not {"source_sha256", "source_type", "source_path"} & set(rebuilt["provenance"])
    assert rebuilt["provenance"]["content_sha256"]


def test_a_legacy_conversions_retry_keeps_its_frontmatter_source_fields(ingest_env, monkeypatch):
    """A pre-R6 conversion still carries `_source_sha256` (and friends) in
    the markdown's own frontmatter when nothing was staged yet."""
    from artmind.document_identity import compute_content_sha256

    vault, source = ingest_env
    derived = ing.MARKDOWNS_DIR / "deck.md"
    derived.write_text(
        "---\n_artmind_id: 0192legacy\n_version: 3\n_domain: general\n_source_sha256: abc123\n"
        f"_content_sha256: {compute_content_sha256('# Deck\n')}\n"
        "_source_type: pptx\n_source_path: _external_docs/0123/deck.pptx\n---\n\n# Deck\n"
    )
    chunks = ing.MARKDOWNS_DIR / "deck_chunks"
    chunks.mkdir()
    (chunks / "chunk_001.md").write_text("# Deck")
    ing._register_document("general", derived, "0192legacy")

    rebuilt = ing._build_file_result_from_db("deck.md", "general")

    assert rebuilt["version"] == 3
    provenance = rebuilt["provenance"]
    assert (provenance["source_sha256"], provenance["source_type"], provenance["source_path"]) == (
        "abc123", "pptx", "_external_docs/0123/deck.pptx",
    )


def test_a_vault_native_retry_records_the_notes_current_path(tmp_path, monkeypatch):
    """The staged document.json may still name the path from before a
    `git mv`; the note's own current path is the truth."""
    v, doc, first = _ingest_vault_doc(tmp_path, monkeypatch)
    folder = stage_as_extracted(ing.KG_DIR, first)
    staged = json.loads((folder / "document.json").read_text())
    (folder / "document.json").write_text(json.dumps({**staged, "source_path": "notes/before_the_move.md"}))

    rebuilt = ing._build_file_result_from_db("retry_doc.md", "general")

    assert rebuilt["provenance"]["source_path"] == "notes/retry_doc.md"
    assert rebuilt["provenance"]["source_type"] == "md"
