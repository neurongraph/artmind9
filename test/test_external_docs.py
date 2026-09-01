"""External sources are copied into the vault under path identity."""
from __future__ import annotations

from pathlib import Path

from artmind.ingest import external_copy_path
from conftest import _fake_docling


def test_a_source_is_copied_under_the_vault(tmp_path):
    dest = external_copy_path(Path("/somewhere/else/deck.pptx"), tmp_path)

    assert dest.is_relative_to(tmp_path / "_external_docs")
    assert dest.name == "deck.pptx"


def test_the_same_path_maps_to_the_same_destination(tmp_path):
    """Re-ingesting an edited file must land on its previous copy, so git shows
    a new version rather than a new document."""
    a = external_copy_path(Path("/somewhere/deck.pptx"), tmp_path)
    b = external_copy_path(Path("/somewhere/deck.pptx"), tmp_path)

    assert a == b


def test_different_sources_with_the_same_name_do_not_collide(tmp_path):
    """Two different decks both called deck.pptx are different documents, not
    versions of each other."""
    a = external_copy_path(Path("/team-a/deck.pptx"), tmp_path)
    b = external_copy_path(Path("/team-b/deck.pptx"), tmp_path)

    assert a != b
    assert a.name == b.name == "deck.pptx"


def test_the_destination_is_stable_across_runs(tmp_path):
    """Derived from the path alone, so it does not depend on what is already
    on disk -- otherwise the mapping would drift as files come and go."""
    first = external_copy_path(Path("/somewhere/deck.pptx"), tmp_path)
    (first.parent).mkdir(parents=True, exist_ok=True)
    first.write_bytes(b"x")

    assert external_copy_path(Path("/somewhere/deck.pptx"), tmp_path) == first


def test_a_vault_resident_source_is_not_copied(env, monkeypatch):
    """The vault copy IS the source, and git already versions it."""
    import artmind.ingest as ing

    vault, _source = env
    resident = vault / "area1" / "deck.pptx"
    resident.parent.mkdir(parents=True)
    resident.write_bytes(b"fake binary v1")

    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))

    result = ing.ingest_file(resident, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "ok"
    assert not (vault / "_external_docs").exists() or not any(
        (vault / "_external_docs").rglob("*")
    )
    # The source is still the one and only copy -- untouched, unmoved.
    assert resident.exists()
    assert resident.read_bytes() == b"fake binary v1"


def test_an_external_source_is_copied_in(env, monkeypatch):
    """Nothing else in the vault records what was ingested."""
    import artmind.ingest as ing

    vault, source = env
    monkeypatch.setattr(ing, "_convert_binary_via_docling", _fake_docling(["# Deck\n\nBody v1.\n"]))

    ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    expected = ing.external_copy_path(source, vault)
    assert expected.is_relative_to(vault / "_external_docs")
    assert expected.exists()
    assert expected.read_bytes() == source.read_bytes()


def test_an_ad_hoc_markdown_outside_the_vault_is_also_copied_in(env, tmp_path):
    """A `.md` outside the vault is not vault-native (`_is_vault_native_markdown`
    requires it to live inside the vault) and it's not a binary either, so it
    goes through `_ingest_binary_or_adhoc`, not `_ingest_binary_derived` --
    that path must land it under `_external_docs/` too."""
    import artmind.ingest as ing

    vault, _source = env
    adhoc = tmp_path / "notes" / "loose.md"
    adhoc.parent.mkdir(parents=True)
    adhoc.write_text("# Loose\n\nSome text.\n", encoding="utf-8")

    result = ing.ingest_file(adhoc, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "ok"
    expected = ing.external_copy_path(adhoc, vault)
    assert expected.is_relative_to(vault / "_external_docs")
    assert expected.exists()
    assert expected.read_text(encoding="utf-8") == adhoc.read_text(encoding="utf-8")


def test_no_vault_configured_falls_back_to_originals_dir(tmp_path, monkeypatch):
    """`_ingest_binary_or_adhoc`'s other real case (docs/vault.md's ownership
    rule only applies inside a vault): with no vault configured at all there
    is nowhere to put `_external_docs/`, so the pre-vault flow -- a plain
    data-dir copy under `ORIGINALS_DIR` -- must still work."""
    import artmind.ingest as ing

    monkeypatch.setattr(ing, "ARTMIND_VAULT_DIR", None)
    originals = tmp_path / "data" / "originals"
    markdowns = tmp_path / "data" / "markdowns"
    originals.mkdir(parents=True)
    markdowns.mkdir(parents=True)
    monkeypatch.setattr(ing, "ORIGINALS_DIR", originals)
    monkeypatch.setattr(ing, "MARKDOWNS_DIR", markdowns)
    import artmind.db as db

    monkeypatch.setattr(db, "DB_PATH", tmp_path / "registry.db")

    source = tmp_path / "incoming" / "note.md"
    source.parent.mkdir(parents=True)
    source.write_text("# Note\n\nBody.\n", encoding="utf-8")

    result = ing.ingest_file(source, "gemma4:e4b", "general", chunk_size=6000)

    assert result["status"] == "ok"
    assert (originals / "note.md").exists()
    assert not (tmp_path / "_external_docs").exists()
