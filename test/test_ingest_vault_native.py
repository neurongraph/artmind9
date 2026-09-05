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


def test_reingest_truly_unchanged_does_not_rewrite_or_commit(vault):
    """Regression: decide_version's "metadata_only" tier only ever compares
    the BODY, so it can't by itself tell "nothing differs" from "only
    frontmatter differs" (docs/document-identity.md's own separate rows) --
    without frontmatter_unchanged() splitting these apart, _ingested_at/
    _source_commit refreshing unconditionally meant a truly unedited file
    still got rewritten (and committed) on every single sync, forever.

    tier stays "metadata_only" either way (decide_version's own vocabulary is
    unchanged) -- what changes is that a truly no-op touch no longer sets
    touched_path, so the caller never calls commit_paths for it."""
    v, doc = vault
    r1 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)
    before = doc.read_text()

    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["resolution_verdict"] == "reingest"
    assert r2["tier"] == "metadata_only"
    assert r2["version"] == r1["version"]
    assert r2["artmind_id"] == r1["artmind_id"]
    assert "touched_path" not in r2, "truly unchanged must not trigger a git commit"
    assert doc.read_text() == before, "the file must not be rewritten at all"


def test_hand_editing_an_authored_field_still_pushes_to_the_graph(vault, monkeypatch):
    """The one place this fix could have silently regressed: a human
    hand-edits `tags` (or title/project/area) without touching the body.
    frontmatter_unchanged() can't actually see this edit as a "change" --
    by the time this function reads the file, the edit is already IN
    existing_meta (parsed fresh off disk), and build_frontmatter only ever
    carries authored fields forward from existing_meta, never independently
    recomputing them. So the file-rewrite/commit is (harmlessly) skipped
    here too -- but apply_metadata_only must still run unconditionally for
    every metadata_only tier, using new_meta's CURRENT (edited) values, or
    the edit would never reach the graph until the next real content change."""
    v, doc = vault
    ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    from artmind.document_identity import render_document

    meta, body = ing._parse_md_frontmatter(doc.read_text())
    meta["tags"] = ["urgent"]
    doc.write_text(render_document(meta, body))

    calls = []
    monkeypatch.setattr("artmind.delta.apply_metadata_only", lambda **k: calls.append(k) or {})

    r2 = ing.ingest_file(doc, "gemma4:e4b", "general", chunk_size=6000)

    assert r2["tier"] == "metadata_only"
    assert "touched_path" not in r2, "no new bytes for artmind itself to commit"
    assert len(calls) == 1
    assert calls[0]["metadata"]["tags"] == ["urgent"], (
        "the graph must still see the human's edit even though the file wasn't rewritten"
    )


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


# ── the metadata-only path must not look in MARKDOWNS_DIR ───────────────────


def test_ingest_to_kg_resolves_a_vault_native_markdown_at_its_vault_path(tmp_path, monkeypatch):
    """Phase 2 stopped copying vault-native markdown into the data dir — the
    vault file IS the markdown. `ingest_to_kg`'s back-compat branch still built
    `MARKDOWNS_DIR / f"{stem}.md"` by hand, so any vault-native re-ingest that
    reached it died on "Markdown not found".

    That is how a live run lost data: an unchanged file returns the
    `metadata_only` tier with no `chunks_dir`, fell into this branch, failed,
    wrote no observations — and the deferred full rebuild then correctly
    deleted every entity whose observations had been cleaned.
    """
    import artmind.ingest as ing

    vault_file = tmp_path / "vault" / "rates.md"
    vault_file.parent.mkdir(parents=True)
    vault_file.write_text("---\n_artmind_id: abc\n---\n\n# Rates\n\nBody text.\n", encoding="utf-8")

    # Point MARKDOWNS_DIR somewhere that definitively does NOT hold a copy.
    monkeypatch.setattr(ing, "MARKDOWNS_DIR", tmp_path / "data" / "markdowns")
    (tmp_path / "data" / "markdowns").mkdir(parents=True)

    seen: dict = {}
    monkeypatch.setattr(ing, "extract_kg", lambda fr, *a, **k: seen.setdefault("fr", fr) and None or None)
    monkeypatch.setattr(ing, "_persist_chunks", lambda chunks, d: d.mkdir(parents=True, exist_ok=True))

    # No chunks_dir — exactly what the metadata_only fast path returns.
    file_result = {
        "artmind_id": "abc",
        "version": 1,
        "registered_path": str(vault_file),
        "source_type": "md",
    }
    ing.ingest_to_kg(file_result, "banking.reference", stage_only=True)

    assert "chunks_dir" in file_result, (
        "the vault file should have been chunked; instead the markdown lookup failed"
    )


def test_the_markdown_lookup_uses_the_shared_resolver():
    """A guard on the fix, not the symptom: hand-building the path here is what
    broke, and `markdown_path_for` exists to be the one place that knows."""
    import inspect

    import artmind.ingest as ing

    src = inspect.getsource(ing.ingest_to_kg)
    back_compat = src[src.index("Back-compat: if ingest_file didn't split chunks"):]
    assert "markdown_path_for(" in back_compat
    assert 'MARKDOWNS_DIR / f"{registered_path.stem}.md"' not in back_compat
