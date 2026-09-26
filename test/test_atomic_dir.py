"""artmind.atomic_dir: atomic staging-folder swap (spec 2026-09-26 §5 R2)."""
from pathlib import Path

import pytest

from artmind.atomic_dir import is_scratch, recover, write_dir_atomic


def _siblings(target: Path) -> list[str]:
    return sorted(p.name for p in target.parent.iterdir())


def test_creates_a_new_folder_with_no_scratch_left_behind(tmp_path):
    target = tmp_path / "general" / "doc"

    write_dir_atomic(target, {"document.json": '{"id": "x"}', "observations.json": b"[]"})

    assert (target / "document.json").read_text() == '{"id": "x"}'
    assert (target / "observations.json").read_bytes() == b"[]"
    assert _siblings(target) == ["doc"]


def test_replaces_named_files_and_carries_everything_else_over(tmp_path):
    target = tmp_path / "doc"
    (target / "chunks" / "sha1").mkdir(parents=True)
    (target / "chunks" / "sha1" / "0.json").write_text("cached chunk")
    (target / "embeddings.json").write_text("vectors")
    (target / "observations.json").write_text("v1")

    write_dir_atomic(target, {"observations.json": "v2"})

    assert (target / "observations.json").read_text() == "v2"
    assert (target / "chunks" / "sha1" / "0.json").read_text() == "cached chunk"
    assert (target / "embeddings.json").read_text() == "vectors"
    assert _siblings(target) == ["doc"]


def test_interrupted_between_the_two_renames_the_next_write_recovers(tmp_path, monkeypatch):
    """Spec §11 "Atomic write": a crash between the renames leaves the old
    folder whole in `.old` and the new one whole in `.tmp`, never a mix. The
    old content in `.old` must be untouched: proof that writing the new
    file did not write through a hardlink into the old inode."""
    target = tmp_path / "doc"
    target.mkdir()
    (target / "observations.json").write_text("v1")
    (target / "document.json").write_text("d1")
    tmp = tmp_path / "doc.tmp"

    real_rename = Path.rename

    def crash_on_second_rename(self, dest):
        if self == tmp:
            raise OSError("simulated crash")
        return real_rename(self, dest)

    monkeypatch.setattr(Path, "rename", crash_on_second_rename)
    with pytest.raises(OSError, match="simulated crash"):
        write_dir_atomic(target, {"observations.json": "v2", "document.json": "d2"})
    monkeypatch.setattr(Path, "rename", real_rename)

    old = tmp_path / "doc.old"
    assert not target.exists()
    assert (old / "observations.json").read_text() == "v1"
    assert (old / "document.json").read_text() == "d1"
    assert (tmp / "observations.json").read_text() == "v2"
    assert (tmp / "document.json").read_text() == "d2"

    recover(target)

    # `.tmp` is only swapped in once complete, so recovery finishes the swap.
    assert (target / "observations.json").read_text() == "v2"
    assert (target / "document.json").read_text() == "d2"
    assert _siblings(target) == ["doc"]


def test_a_stale_tmp_from_a_crash_while_building_is_discarded(tmp_path):
    target = tmp_path / "doc"
    target.mkdir()
    (target / "observations.json").write_text("v1")
    stale = tmp_path / "doc.tmp"
    stale.mkdir()
    (stale / "observations.json").write_text("half-written")
    (stale / "junk.json").write_text("junk")

    write_dir_atomic(target, {"document.json": "d2"})

    assert (target / "observations.json").read_text() == "v1"
    assert (target / "document.json").read_text() == "d2"
    assert not (target / "junk.json").exists()
    assert _siblings(target) == ["doc"]


def test_a_stale_old_left_after_a_completed_swap_is_removed(tmp_path):
    target = tmp_path / "doc"
    target.mkdir()
    (target / "observations.json").write_text("v2")
    (tmp_path / "doc.old").mkdir()

    recover(target)

    assert (target / "observations.json").read_text() == "v2"
    assert _siblings(target) == ["doc"]


def test_is_scratch():
    assert is_scratch(Path("kg/general/doc.tmp"))
    assert is_scratch(Path("kg/general/doc.old"))
    assert not is_scratch(Path("kg/general/doc"))
    assert not is_scratch(Path("kg/general/table__accounts"))
