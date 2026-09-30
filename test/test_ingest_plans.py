"""The ingest's "would this do work?" decisions, factored out so `ingest
pending` (spec 2026-09-30 §6 X2) reuses them instead of copying them:
`ingest.plan_vault_native`, `ingest.plan_binary` and
`structured.pipeline.registered_tables`/`is_unchanged`.

Each planner must (a) write nothing and (b) decide exactly what the real
ingest then does -- asserted here by planning, ingesting for real, and
comparing."""
import pytest

import artmind.ingest as ing
from conftest import _fake_docling, stage_as_extracted


def _note(vault, rel, text, newline=None):
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline=newline) as f:
        f.write(text)
    return path


# ── notes ────────────────────────────────────────────────────────────────────


def test_a_new_note_plans_content_and_writes_nothing(ingest_env):
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", "Hello.\n")

    plan = ing.plan_vault_native(note, domain="general")

    assert (plan.tier, plan.is_new, plan.stamp) == ("content", True, True)
    assert plan.resolution.verdict == "new"
    assert note.read_text() == "Hello.\n", "planning never stamps the note"
    from artmind.db import _registry_row_by_path
    from artmind.document_identity import canonical_path

    assert _registry_row_by_path(canonical_path(note)) is None, "planning never registers"


def test_an_ingested_unchanged_note_plans_metadata_only(ingest_env):
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", "Hello.\n")
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(note, "m", "general"))

    plan = ing.plan_vault_native(note, domain="general")

    assert (plan.tier, plan.is_new, plan.stamp) == ("metadata_only", False, False)
    assert plan.resolution.verdict == "reingest"


def test_an_edited_body_plans_content_but_not_new(ingest_env):
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", "Hello.\n")
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(note, "m", "general"))
    note.write_text(note.read_text().replace("Hello.", "Hello again."))

    plan = ing.plan_vault_native(note, domain="general")

    assert (plan.tier, plan.is_new) == ("content", False)
    assert plan.version.version == 2


def test_a_domain_change_plans_content(ingest_env):
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", "Hello.\n")
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(note, "m", "general"))

    plan = ing.plan_vault_native(note, domain="general", set_domain="banking")

    assert plan.domain_changed is True
    assert plan.tier == "content"
    assert plan.version.tier == "metadata_only", "the body itself did not change"


@pytest.mark.parametrize(
    "text, newline",
    [
        ("\n\nStarts with blank lines.\n", None),
        ("Line one.\r\nLine two.\r\n", ""),
        ("---\ntitle: kept\n---\nBody under a title.\n", None),
    ],
)
def test_the_planned_hash_is_the_one_the_ingest_records(ingest_env, text, newline):
    """The body is hashed as it stands AFTER the identity stamp -- which can
    differ from the note's body before it (leading blank lines are dropped
    once a frontmatter block precedes them; CRLF reads back as LF). The plan
    computes the stamped text without writing it."""
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", text, newline=newline)

    from artmind.document_identity import compute_content_sha256

    plan = ing.plan_vault_native(note, domain="general")
    result = ing.ingest_file(note, "m", "general")
    _, body_on_disk = ing._parse_md_frontmatter(note.read_text(encoding="utf-8"))

    assert plan.version.content_sha256 == compute_content_sha256(body_on_disk)
    assert result["content_sha256"] == compute_content_sha256(body_on_disk)


def test_a_domain_that_is_not_a_folder_name_is_a_plan_error(ingest_env):
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", "---\n_domain: ../up\n---\n\nBody.\n")

    with pytest.raises(ing.NotePlanError, match="not a plain folder name"):
        ing.plan_vault_native(note, domain="general")


def test_the_ingest_reports_a_plan_error_as_before(ingest_env):
    vault, _ = ingest_env
    note = _note(vault, "notes/a.md", "No domain anywhere.\n")

    result = ing.ingest_file(note, "m", None)

    assert result["status"] == "failed"
    assert result["error"] == f"{note}: no '_domain' in frontmatter and no --domain given"


# ── binaries ─────────────────────────────────────────────────────────────────


def test_a_binary_plans_new_then_unchanged_then_changed(ingest_env, monkeypatch):
    vault, _ = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nv1\n"]))
    deck = vault / "decks" / "deck.pptx"
    deck.parent.mkdir()
    deck.write_bytes(b"v1")

    first = ing.plan_binary(deck, "general")
    assert (first.is_new, first.unchanged) == (True, False)
    assert not first.registered_path.exists(), "planning never converts"

    stage_as_extracted(ing.KG_DIR, ing.ingest_file(deck, "m", "general"))
    again = ing.plan_binary(deck, "general")
    assert (again.is_new, again.unchanged) == (False, True)

    deck.write_bytes(b"v2")
    changed = ing.plan_binary(deck, "general")
    assert (changed.is_new, changed.unchanged) == (False, False)


def test_an_unchanged_binary_plan_is_the_ingests_no_op(ingest_env, monkeypatch):
    vault, _ = ingest_env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nv1\n"]))
    deck = vault / "deck.pptx"
    deck.write_bytes(b"v1")
    stage_as_extracted(ing.KG_DIR, ing.ingest_file(deck, "m", "general"))

    assert ing.plan_binary(deck, "general").unchanged
    assert ing.ingest_file(deck, "m", "general")["tier"] == "no_op"


# ── tables ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def stores(tmp_path, monkeypatch):
    import artmind.db as db
    import paths

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")
    monkeypatch.setattr(paths, "STRUCTURED_DIR", tmp_path / "structured")
    return tmp_path


def test_a_table_file_is_unchanged_only_when_every_table_holds_its_bytes(stores):
    from artmind.ingest import _compute_sha256
    from artmind.structured.pipeline import ingest_structured_file, is_unchanged, registered_tables

    source = stores / "accounts.csv"
    source.write_text("id\n1\n")
    specs, rows = registered_tables(source, "banking")
    assert [s["table_name"] for s in specs] == ["accounts"]
    assert rows == [None]
    assert not is_unchanged(rows, _compute_sha256(source))

    ingest_structured_file(source, "banking")
    _, rows = registered_tables(source, "banking")
    assert is_unchanged(rows, _compute_sha256(source))
    assert ingest_structured_file(source, "banking")["status"] == "skipped"

    source.write_text("id\n1\n2\n")
    _, rows = registered_tables(source, "banking")
    assert not is_unchanged(rows, _compute_sha256(source))


def test_no_tables_is_never_unchanged():
    from artmind.structured.pipeline import is_unchanged

    assert is_unchanged([], "abc") is False
