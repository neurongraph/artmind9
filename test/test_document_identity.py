"""Tests for artmind.document_identity: the Phase 2 resolution table,
versioning, and frontmatter contract (docs/document-identity.md)."""

import uuid
from pathlib import Path

import pytest

from artmind.document_identity import (
    AUTHORED_FIELDS,
    IDENTITY_FIELDS,
    IdentityConflict,
    MOVED_FIELDS,
    Resolution,
    SYSTEM_FIELDS,
    canonical_path,
    compute_content_sha256,
    decide_version,
    ingest_baseline,
    lift_declared_version,
    markdown_path_for,
    mint_artmind_id,
    needs_stamp,
    render_document,
    resolve_identity,
    serialize_frontmatter,
    write_document,
)


def test_mint_artmind_id_is_a_valid_uuid7():
    value = mint_artmind_id()
    parsed = uuid.UUID(value)
    assert parsed.version == 7
    assert str(parsed) == value  # bare, no prefix


def test_mint_artmind_id_is_time_ordered():
    ids = [mint_artmind_id() for _ in range(5)]
    assert ids == sorted(ids)


def test_compute_content_sha256_matches_hashlib():
    import hashlib

    assert compute_content_sha256("hello") == hashlib.sha256(b"hello").hexdigest()


def test_compute_content_sha256_ignores_frontmatter_by_construction():
    # The function only ever sees the body -- this documents the contract at
    # the call site, since compute_content_sha256 itself has no frontmatter
    # awareness (the caller is responsible for stripping it first).
    assert compute_content_sha256("body only") != compute_content_sha256("---\nx: 1\n---\nbody only")


def test_canonical_path_relative_to_vault(tmp_path, monkeypatch):
    import artmind.document_identity as di

    vault = tmp_path / "vault"
    vault.mkdir()
    f = vault / "policies" / "foo.md"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", vault)

    assert canonical_path(f) == "policies/foo.md"


def test_canonical_path_falls_back_to_absolute_outside_vault(tmp_path, monkeypatch):
    import artmind.document_identity as di

    vault = tmp_path / "vault"
    vault.mkdir()
    outside = tmp_path / "elsewhere" / "foo.md"
    outside.parent.mkdir()
    outside.write_text("x")
    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", vault)

    assert canonical_path(outside) == str(outside.resolve())


def test_canonical_path_no_vault_configured(tmp_path, monkeypatch):
    import artmind.document_identity as di

    f = tmp_path / "foo.md"
    f.write_text("x")
    monkeypatch.setattr(di, "ARTMIND_VAULT_DIR", None)

    assert canonical_path(f) == str(f.resolve())


# ── resolution table ─────────────────────────────────────────────────────────


def _stub_registry(monkeypatch, by_id=None, by_path=None):
    import artmind.document_identity as di

    monkeypatch.setattr(di, "_registry_row_by_artmind_id", lambda aid: by_id)
    monkeypatch.setattr(di, "_registry_row_by_path", lambda p: by_path)


def test_resolve_new_when_no_id_and_no_registered_path(tmp_path, monkeypatch):
    _stub_registry(monkeypatch)
    result = resolve_identity(tmp_path / "fresh.md", frontmatter_id=None)
    assert result.verdict == "new"
    uuid.UUID(result.artmind_id)  # minted, valid


def test_resolve_heal_when_no_id_but_path_registered(tmp_path, monkeypatch):
    _stub_registry(monkeypatch, by_path={"artmind_id": "known-id", "path": "x.md"})
    result = resolve_identity(tmp_path / "x.md", frontmatter_id=None)
    assert result == Resolution(verdict="heal", artmind_id="known-id")


def test_resolve_reingest_when_id_matches_registered_path(tmp_path, monkeypatch):
    import artmind.document_identity as di

    path = tmp_path / "x.md"
    monkeypatch.setattr(di, "canonical_path", lambda p: "x.md")
    _stub_registry(monkeypatch, by_id={"artmind_id": "id-1", "path": "x.md"})
    result = resolve_identity(path, frontmatter_id="id-1")
    assert result == Resolution(verdict="reingest", artmind_id="id-1")


def test_resolve_adopt_when_id_present_but_unknown_to_registry(tmp_path, monkeypatch):
    _stub_registry(monkeypatch, by_id=None)
    result = resolve_identity(tmp_path / "x.md", frontmatter_id="orphan-id")
    assert result == Resolution(verdict="adopt", artmind_id="orphan-id")


def test_resolve_move_when_old_path_gone(tmp_path, monkeypatch):
    import artmind.document_identity as di

    monkeypatch.setattr(di, "canonical_path", lambda p: "new/path.md")
    monkeypatch.setattr(di, "_path_exists", lambda p: False)
    _stub_registry(monkeypatch, by_id={"artmind_id": "id-1", "path": "old/path.md"})
    result = resolve_identity(tmp_path / "new" / "path.md", frontmatter_id="id-1")
    assert result == Resolution(verdict="move", artmind_id="id-1", prior_path="old/path.md")


def test_resolve_refuse_when_old_path_still_exists(tmp_path, monkeypatch):
    import artmind.document_identity as di

    monkeypatch.setattr(di, "canonical_path", lambda p: "new/path.md")
    monkeypatch.setattr(di, "_path_exists", lambda p: True)
    _stub_registry(monkeypatch, by_id={"artmind_id": "id-1", "path": "old/path.md"})

    with pytest.raises(IdentityConflict) as exc_info:
        resolve_identity(tmp_path / "new" / "path.md", frontmatter_id="id-1")
    assert exc_info.value.artmind_id == "id-1"
    assert exc_info.value.existing_path == "old/path.md"


def test_resolve_refuse_with_fork_mints_a_fresh_id(tmp_path, monkeypatch):
    import artmind.document_identity as di

    monkeypatch.setattr(di, "canonical_path", lambda p: "new/path.md")
    monkeypatch.setattr(di, "_path_exists", lambda p: True)
    _stub_registry(monkeypatch, by_id={"artmind_id": "id-1", "path": "old/path.md"})

    result = resolve_identity(tmp_path / "new" / "path.md", frontmatter_id="id-1", fork=True)
    assert result.verdict == "new"
    assert result.artmind_id != "id-1"


def test_resolve_refuse_with_adopt_transfers_identity(tmp_path, monkeypatch):
    import artmind.document_identity as di

    monkeypatch.setattr(di, "canonical_path", lambda p: "new/path.md")
    monkeypatch.setattr(di, "_path_exists", lambda p: True)
    _stub_registry(monkeypatch, by_id={"artmind_id": "id-1", "path": "old/path.md"})

    result = resolve_identity(tmp_path / "new" / "path.md", frontmatter_id="id-1", adopt=True)
    assert result == Resolution(verdict="move", artmind_id="id-1", prior_path="old/path.md")


# ── versioning ───────────────────────────────────────────────────────────────


def test_decide_version_first_ingest_is_content_version_1():
    decision = decide_version("body text", existing_meta={})
    assert decision.tier == "content"
    assert decision.version == 1


def test_decide_version_body_changed_bumps_version():
    prior_sha = compute_content_sha256("old body")
    decision = decide_version("new body", existing_meta={"_content_sha256": prior_sha, "_version": 3})
    assert decision.tier == "content"
    assert decision.version == 4


def test_decide_version_body_unchanged_is_metadata_only():
    sha = compute_content_sha256("same body")
    decision = decide_version("same body", existing_meta={"_content_sha256": sha, "_version": 2})
    assert decision.tier == "metadata_only"
    assert decision.version == 2


def test_decide_version_without_a_prior_hash_continues_the_staged_version():
    """A pre-R6 staging folder has a version but no hash, and a migrated note
    no longer says: the next ingest is a content change at version + 1, never
    a restart at 1 that would reuse an old version's observation ids."""
    decision = decide_version("body", existing_meta={"_content_sha256": None, "_version": 3})
    assert decision.tier == "content"
    assert decision.version == 4


# ── the baseline: document.json first, legacy frontmatter as a fallback ─────


def test_ingest_baseline_is_document_json_once_it_records_a_hash():
    staged = {"id": "id-1", "version": 5, "content_sha256": "new", "source_sha256": "bin"}
    fat = {"_content_sha256": "old", "_version": 2, "_source_sha256": "oldbin"}

    assert ingest_baseline(staged, fat) == {"_content_sha256": "new", "_version": 5, "_source_sha256": "bin"}


def test_ingest_baseline_falls_back_to_legacy_frontmatter():
    staged = {"id": "id-1", "version": 2}  # pre-R6 document.json: no hash
    fat = {"_content_sha256": "old", "_version": 2, "_source_sha256": "oldbin"}

    assert ingest_baseline(staged, fat) == {"_content_sha256": "old", "_version": 2, "_source_sha256": "oldbin"}
    assert ingest_baseline(None, {}) == {"_content_sha256": None, "_version": None, "_source_sha256": None}
    assert ingest_baseline(staged, {"_artmind_id": "id-1"})["_version"] == 2


def test_ingest_baseline_prefers_the_staged_version_and_fills_a_missing_one_from_the_note():
    both = ingest_baseline({"version": 5, "content_sha256": "h"}, {"_version": 2, "_content_sha256": "old"})
    assert both["_version"] == 5

    hash_only = ingest_baseline({"content_sha256": "h"}, {"_version": 2})
    assert hash_only == {"_content_sha256": "h", "_version": 2, "_source_sha256": None}

    no_hash = ingest_baseline({"version": 5}, {"_version": 2, "_content_sha256": "old"})
    assert no_hash["_version"] == 2  # the note's own version wins while it has one


def test_needs_stamp_only_for_missing_or_changed_identity():
    assert needs_stamp({}, "id-1", "general") is True
    assert needs_stamp({"_artmind_id": "id-1"}, "id-1", "general") is True
    assert needs_stamp({"_artmind_id": "id-1", "_domain": "other"}, "id-1", "general") is True
    assert needs_stamp({"_artmind_id": "id-0", "_domain": "general"}, "id-1", "general") is True
    assert needs_stamp({"_artmind_id": "id-1", "_domain": "general", "_version": 9, "tags": ["x"]}, "id-1", "general") is False


def test_moved_fields_are_system_fields_and_never_identity():
    assert set(MOVED_FIELDS) <= set(SYSTEM_FIELDS)
    assert not set(MOVED_FIELDS) & set(IDENTITY_FIELDS)
    assert set(IDENTITY_FIELDS) <= set(SYSTEM_FIELDS)
    assert IDENTITY_FIELDS == ("_artmind_id", "_domain")
    assert MOVED_FIELDS == {
        "_version": "version",
        "_content_sha256": "content_sha256",
        "_source_sha256": "source_sha256",
        "_source_commit": "source_commit",
        "_ingested_at": "ingested_at",
        "_source_path": "source_path",
        "_source_type": "source_type",
        "_status": None,
    }


# ── frontmatter contract ─────────────────────────────────────────────────────


def test_lift_declared_version_from_table_header():
    body = "# Doc\n\n| Field | Value |\n|---|---|\n| Version | 2.1 |\n"
    assert lift_declared_version(body) == "2.1"


def test_lift_declared_version_keeps_annotation_verbatim():
    # Unlike system _version, declared_version carries no system meaning --
    # no numeric stripping of a trailing annotation.
    body = "| Version | 1.0 (Updated Monthly) |\n"
    assert lift_declared_version(body) == "1.0 (Updated Monthly)"


def test_lift_declared_version_absent_returns_none():
    assert lift_declared_version("# No version header here\n") is None


def test_serialize_frontmatter_orders_system_then_authored_then_extra():
    meta = {"custom_field": "z", "title": "T", "_artmind_id": "id-1", "_version": 1}
    rendered = serialize_frontmatter(meta)
    assert rendered.index("_artmind_id") < rendered.index("title") < rendered.index("custom_field")


def test_serialize_frontmatter_never_drops_unknown_keys():
    meta = {"_artmind_id": "id-1", "some_domain_specific_key": "value"}
    rendered = serialize_frontmatter(meta)
    assert "some_domain_specific_key: value" in rendered


def test_render_document_round_trips_with_parse_md_frontmatter():
    from artmind.ingest import _parse_md_frontmatter

    meta = {"_artmind_id": "id-1", "_version": 1, "title": "Foo"}
    body = "# Foo\n\nSome content.\n"
    text = render_document(meta, body)

    parsed_meta, parsed_body = _parse_md_frontmatter(text)
    assert parsed_meta["_artmind_id"] == "id-1"
    assert parsed_meta["title"] == "Foo"
    assert parsed_body == body


def test_write_document_writes_to_disk(tmp_path):
    path = tmp_path / "doc.md"
    write_document(path, {"_artmind_id": "id-1"}, "body\n")
    assert "id-1" in path.read_text()
    assert path.read_text().endswith("body\n")


# ── markdown_path_for ────────────────────────────────────────────────────────


def test_markdown_path_for_vault_native_returns_vault_path(tmp_path):
    vault_path = tmp_path / "vault" / "foo.md"
    assert markdown_path_for("md", vault_path=vault_path) == vault_path


def test_markdown_path_for_binary_uses_data_dir_stem():
    from paths import MARKDOWNS_DIR

    assert markdown_path_for("pptx", stem="deck") == MARKDOWNS_DIR / "deck.md"


def test_markdown_path_for_md_without_vault_path_raises():
    with pytest.raises(ValueError):
        markdown_path_for("md")


def test_markdown_path_for_binary_without_stem_raises():
    with pytest.raises(ValueError):
        markdown_path_for("pptx")
