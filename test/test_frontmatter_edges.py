"""Edge cases for the line-level note editor (`artmind/frontmatter.py`): input
the YAML loader rejects oddly, a BOM, a symlinked note, the scratch file's
permissions and location, and the atomic-write ordering."""
import errno
import os
import stat

import pytest

from artmind import frontmatter as fm

USER_BLOCK = (
    "---\n"
    'title: "My note"   # my own comment\n'
    "tags:\n"
    "- alpha\n"
    "- beta\n"
    "aliases: [x, y]\n"
    "---\n"
)
BODY = "\n# Heading\n"


def test_a_column_zero_block_list_goes_with_its_key():
    text = USER_BLOCK + BODY
    assert fm.remove_fields(text, ["tags"]) == (
        "---\n" 'title: "My note"   # my own comment\n' "aliases: [x, y]\n" "---\n" + BODY
    )
    assert fm.set_fields(text, {"tags": "solo"}) == (
        "---\n" 'title: "My note"   # my own comment\n' "tags: solo\n" "aliases: [x, y]\n" "---\n" + BODY
    )


def test_a_value_that_does_not_round_trip_is_refused_with_and_without_frontmatter():
    with pytest.raises(fm.FrontmatterEditError):
        fm.set_fields("Body\n", {"_domain": "a\x85b"})       # PyYAML folds \x85 to a space
    with pytest.raises(fm.FrontmatterEditError):
        fm.set_fields("---\na: 1\n---\nBody\n", {"_domain": "a\x85b"})


def test_stamping_and_replacing_inside_a_crlf_block_uses_crlf():
    text = "---\r\ntitle: T\r\n_domain: old\r\n---\r\nB\r\n"
    assert fm.set_fields(text, {"_artmind_id": "i", "_domain": "new"}) == (
        "---\r\n_artmind_id: i\r\ntitle: T\r\n_domain: new\r\n---\r\nB\r\n"
    )


@pytest.mark.parametrize("block", ["d: 2026-02-30\n", "d: !!timestamp 'nope'\n", "d: !!timestamp 5\n"])
def test_yaml_that_the_loader_rejects_with_a_non_yaml_error_is_still_a_frontmatter_error(block):
    text = f"---\n{block}_version: 1\n---\nB\n"
    with pytest.raises(fm.FrontmatterEditError):
        fm.set_fields(text, {"_domain": "x"})
    with pytest.raises(fm.FrontmatterEditError):
        fm.remove_fields(text, ["_version"])


def test_a_bom_note_is_refused_and_never_rewritten(tmp_path):
    text = "﻿---\ntitle: T\n---\nBody\n"
    note = tmp_path / "a.md"
    note.write_bytes(text.encode("utf-8"))

    with pytest.raises(fm.FrontmatterEditError, match="BOM"):
        fm.set_fields(fm.read_note(note), {"_domain": "d"})
    with pytest.raises(fm.FrontmatterEditError, match="BOM"):
        fm.remove_fields(fm.read_note(note), ["title"])

    assert note.read_bytes() == text.encode("utf-8")


def test_remove_fields_rejects_a_bare_string_instead_of_iterating_its_characters():
    with pytest.raises(TypeError):
        fm.remove_fields("---\na: 1\nb: 2\n---\nB\n", "ab")


def test_a_symlinked_note_is_written_through_not_replaced(tmp_path):
    real = tmp_path / "real.md"
    real.write_text("old")
    link = tmp_path / "link.md"
    link.symlink_to(real)

    fm.write_note(link, "new")

    assert link.is_symlink() and real.read_text() == "new"


def test_the_scratch_file_is_never_more_permissive_than_the_note(tmp_path, monkeypatch):
    note = tmp_path / "a.md"
    note.write_text("old")
    note.chmod(0o600)
    seen = []
    real_fsync = os.fsync

    def fsync(fd):
        seen.append(stat.S_IMODE(os.fstat(fd).st_mode))     # the data is on disk now
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    fm.write_note(note, "secret", scratch_dir=tmp_path / "s")

    assert seen == [0o600]
    assert stat.S_IMODE(note.stat().st_mode) == 0o600


def test_a_new_note_gets_the_usual_permissions_not_the_scratch_files(tmp_path):
    old_umask = os.umask(0o022)
    try:
        fm.write_note(tmp_path / "new.md", "x")
    finally:
        os.umask(old_umask)
    assert stat.S_IMODE((tmp_path / "new.md").stat().st_mode) == 0o644


def test_a_scratch_dir_outside_a_vault_is_not_created(tmp_path):
    note = tmp_path / "a.md"
    note.write_text("old")
    scratch = tmp_path / "not-a-vault" / "data"

    fm.write_note(note, "new", scratch_dir=scratch)

    assert note.read_text() == "new"
    assert not (tmp_path / "not-a-vault").exists()
    assert [p.name for p in tmp_path.iterdir()] == ["a.md"]


def test_a_vault_scratch_dir_is_created_when_its_artmind_folder_exists(tmp_path, monkeypatch):
    note = tmp_path / "a.md"
    note.write_text("old")
    (tmp_path / ".artmind").mkdir()
    scratch = tmp_path / ".artmind" / "data"
    used = []
    real_replace = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (used.append(os.path.dirname(a)), real_replace(a, b))[1])

    fm.write_note(note, "new", scratch_dir=scratch)

    assert note.read_text() == "new"
    assert used == [str(scratch)] and list(scratch.iterdir()) == []


def test_the_data_is_fsynced_before_the_rename(tmp_path, monkeypatch):
    note = tmp_path / "a.md"
    note.write_text("old")
    order = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda a, b: (order.append("replace"), real_replace(a, b))[1])
    (tmp_path / ".artmind").mkdir()
    fm.write_note(note, "new", scratch_dir=tmp_path / ".artmind" / "data")
    assert order == ["fsync", "replace"]


def test_a_failed_rename_keeps_the_note_and_leaves_no_scratch(tmp_path, monkeypatch):
    note = tmp_path / "a.md"
    note.write_text("old")
    (tmp_path / ".artmind").mkdir()
    scratch = tmp_path / ".artmind" / "data"
    monkeypatch.setattr(os, "replace", lambda a, b: (_ for _ in ()).throw(OSError(errno.EIO, "boom")))
    with pytest.raises(OSError):
        fm.write_note(note, "new", scratch_dir=scratch)
    assert note.read_text() == "old"
    assert list(scratch.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == [".artmind", "a.md"]


def test_a_cross_device_scratch_falls_back_to_a_temp_file_beside_the_note(tmp_path, monkeypatch):
    note = tmp_path / "a.md"
    note.write_text("old")
    (tmp_path / ".artmind").mkdir()
    scratch = tmp_path / ".artmind" / "data"
    real, calls = os.replace, []

    def replace(a, b):
        calls.append(a)
        if len(calls) == 1:
            raise OSError(errno.EXDEV, "cross-device")
        return real(a, b)

    monkeypatch.setattr(os, "replace", replace)
    fm.write_note(note, "new", scratch_dir=scratch)
    assert note.read_text() == "new"
    assert len(calls) == 2 and os.path.dirname(calls[1]) == str(tmp_path)
    assert list(scratch.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == [".artmind", "a.md"]
