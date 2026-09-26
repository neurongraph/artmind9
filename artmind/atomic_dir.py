"""Atomic replacement of a KG staging folder (spec 2026-09-26 §5 R2).

Obsidian Git auto-commits on a timer, so it can capture a folder mid-write
(a new document.json beside an old observations.json). Every staging writer
builds the complete new folder in `<dir>.tmp/`, fsyncs it, then swaps it in:

    <dir>      -> <dir>.old
    <dir>.tmp  -> <dir>
    remove <dir>.old

A directory rename over a non-empty directory is not atomic on POSIX, hence
three steps. A crash leaves at worst a `.tmp` or `.old` sibling, both
gitignored (R4), and `recover()` -- run at the start of every write --
finishes or discards them.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

TMP_SUFFIX = ".tmp"
OLD_SUFFIX = ".old"


def is_scratch(path: Path) -> bool:
    """Whether `path` is a `.tmp`/`.old` sibling left by `write_dir_atomic`,
    which a directory listing of staging folders should skip."""
    return path.name.endswith((TMP_SUFFIX, OLD_SUFFIX))


def _siblings(target: Path) -> tuple[Path, Path]:
    return target.with_name(target.name + TMP_SUFFIX), target.with_name(target.name + OLD_SUFFIX)


def recover(target: Path) -> None:
    """Finish or discard a swap a crash interrupted.

    `.old` with no `target` means the crash fell between the two renames;
    `.tmp` is only renamed after it is complete and fsynced, so it is
    promoted. A `.tmp` beside an intact `target` is a crash while building
    and is discarded; a `.old` beside `target` is a crash before cleanup."""
    tmp, old = _siblings(target)
    if old.exists() and not target.exists():
        (tmp if tmp.exists() else old).rename(target)
    if tmp.exists():
        shutil.rmtree(tmp)
    if old.exists():
        shutil.rmtree(old)


def _link_or_copy(src: str, dst: str) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:
        pass  # some filesystems refuse fsync on a directory; the rename still orders
    finally:
        os.close(fd)


def write_dir_atomic(target: Path, files: dict[str, str | bytes]) -> None:
    """Replace `files` (name -> content) inside `target` as one swap.

    Every other entry already in `target` -- the per-chunk extraction cache,
    the gitignored embedding sidecar, debug output -- is carried over
    unchanged, as hardlinks. A replaced file is unlinked in `.tmp` before
    it is written, so the write never reaches the old folder's inode."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    recover(target)
    tmp, old = _siblings(target)
    if target.exists():
        shutil.copytree(target, tmp, symlinks=True, copy_function=_link_or_copy)
    else:
        tmp.mkdir()
    for name, content in files.items():
        path = tmp / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        data = content.encode("utf-8") if isinstance(content, str) else content
        with open(path, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    _fsync_dir(tmp)
    if target.exists():
        target.rename(old)
    tmp.rename(target)
    _fsync_dir(target.parent)
    if old.exists():
        shutil.rmtree(old)
