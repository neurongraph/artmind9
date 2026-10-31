"""Document keys: same-named sources must not collide
(spec docs/superpowers/specs/2026-10-31-document-key-collisions-design.md)."""
import json
from hashlib import sha256

import artmind.ingest as ing
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
