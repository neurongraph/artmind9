"""Which file types artmind will attempt (docs/vault.md, "Guardrails")."""
from __future__ import annotations

import pytest

from artmind.ingest import SUPPORTED_SUFFIXES, collect_ingest_files, is_supported


@pytest.mark.parametrize("name", [
    "note.md", "deck.pptx", "paper.pdf", "memo.docx",
    "table.csv", "book.xlsx", "scan.png", "photo.jpg",
])
def test_types_artmind_can_actually_ingest(tmp_path, name):
    assert is_supported(tmp_path / name) is True


@pytest.mark.parametrize("name", [
    "board.canvas",      # Obsidian canvas -- JSON, docling cannot read it
    "sketch.excalidraw",  # likewise
    "archive.zip",
    "video.mp4",
    "notes.txt.bak",
])
def test_types_artmind_must_not_hand_to_docling(tmp_path, name):
    assert is_supported(tmp_path / name) is False


def test_the_suffix_check_is_case_insensitive(tmp_path):
    assert is_supported(tmp_path / "DECK.PPTX") is True


def test_a_directory_walk_skips_unsupported_types(tmp_path):
    (tmp_path / "note.md").write_text("# note")
    (tmp_path / "board.canvas").write_text("{}")
    (tmp_path / "deck.pptx").write_bytes(b"x")

    found = [f.name for f in collect_ingest_files(tmp_path)]

    assert sorted(found) == ["deck.pptx", "note.md"]


def test_a_directory_walk_still_skips_dotfiles(tmp_path):
    """Pre-existing behaviour that must survive: .artmind/, .obsidian/, .git/."""
    (tmp_path / "note.md").write_text("# note")
    (tmp_path / ".artmind").mkdir()
    (tmp_path / ".artmind" / "vault.yaml").write_text("ingest: {}\n")
    (tmp_path / ".obsidian").mkdir()
    (tmp_path / ".obsidian" / "app.json").write_text("{}")

    found = [f.name for f in collect_ingest_files(tmp_path)]

    assert found == ["note.md"]


def test_naming_one_unsupported_file_explicitly_still_returns_it(tmp_path):
    """A directory walk filters silently; naming a file is an explicit request,
    and the caller reports why it cannot be ingested rather than the walk
    pretending it was never there."""
    target = tmp_path / "board.canvas"
    target.write_text("{}")

    assert collect_ingest_files(target) == [target]
