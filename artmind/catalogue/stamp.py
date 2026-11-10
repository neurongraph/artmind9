"""Disk stamps: a cheap fingerprint of the files a catalogue part reads.

Spec `docs/superpowers/specs/2026-11-02-catalogue-and-answer-assets-design.md` §3.
A stamp is (file count, newest mtime in ns, sha1 of the sorted relative names) of
a directory tree: an add, an edit, a rename and a delete each change it, an
untouched tree keeps it. It costs one `stat` per file.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

Stamp = tuple[int, int, str]

EMPTY: Stamp = (0, 0, "")


def stamp(directory: Path | str | None, *, suffix: str | None = None) -> Stamp:
    """The stamp of `directory` (recursive); `EMPTY` when it is absent.
    `suffix` keeps only files ending in it (`.md` for `_Topics/`)."""
    if directory is None:
        return EMPTY
    root = Path(directory)
    if not root.is_dir():
        return EMPTY
    names: list[str] = []
    newest = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for fname in filenames:
            if fname.startswith(".") or (suffix and not fname.endswith(suffix)):
                continue
            full = Path(dirpath) / fname
            try:
                mtime = full.stat().st_mtime_ns
            except OSError:  # removed while walking
                continue
            newest = max(newest, mtime)
            names.append(full.relative_to(root).as_posix())
    if not names:
        return EMPTY
    digest = hashlib.sha1("\n".join(sorted(names)).encode("utf-8")).hexdigest()[:12]
    return (len(names), newest, digest)


def _text(value: Stamp) -> str:
    return f"{value[0]}.{value[1]}.{value[2]}"


def disk_stamps(domain: str) -> dict[str, Stamp]:
    """The stamp of each disk-side input, for `domain`: saved views, topic plans,
    the domain's insight snapshots and the schema files. Read at call time so
    tests can repoint `paths`."""
    import paths

    out = {
        "views": stamp(getattr(paths, "DOMAIN_VIEWS_DIR", None)),
        # `p`lans: the first letter keys a stamp (`part`), so not `t`opic_plans beside nothing else.
        "plans": stamp(getattr(paths, "DOMAIN_TOPIC_PLANS_DIR", None)),
        "schemas": stamp(getattr(paths, "DOMAIN_SCHEMAS_DIR", None)),
    }
    insights = getattr(paths, "INSIGHTS_DIR", None)
    out["insights"] = stamp(Path(insights) / domain) if insights else EMPTY
    return out


def disk_stamp(domain: str) -> str:
    """All of `disk_stamps` as one string, stable across a JSON round trip."""
    return "|".join(f"{k[0]}:{_text(v)}" for k, v in sorted(disk_stamps(domain).items()))


def part(disk_stamp_text: str, name: str) -> str:
    """One input's stamp out of a `disk_stamp` string (`schemas`, `views`, ...)."""
    prefix = f"{name[0]}:"
    for piece in disk_stamp_text.split("|"):
        if piece.startswith(prefix):
            return piece
    return ""
