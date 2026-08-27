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
import subprocess

import artmind.db as db
import artmind.ingest as ing


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
    _v, doc, first = _ingest_vault_doc(tmp_path, monkeypatch)

    doc.write_text(doc.read_text() + "\nA new paragraph.\n")
    second = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    assert second["version"] == first["version"] + 1

    rebuilt = ing._build_file_result_from_db("retry_doc.md", "general")
    assert rebuilt["version"] == second["version"] == 2


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
    monkeypatch.setattr(ing, "build_properties_prompt", lambda text, ents, schema: "p")
    monkeypatch.setattr(ing, "build_relationships_prompt", lambda text, ents, schema: "p")
    monkeypatch.setattr(ing, "_llm_extract", lambda step_name, model, prompt, debug_dir: ([], True))

    file_result = ing._build_file_result_from_db("retry_doc.md", "general")
    assert file_result is not None

    doc_kg_dir = ing.extract_kg(file_result, "general", max_workers=1)

    assert doc_kg_dir is not None
