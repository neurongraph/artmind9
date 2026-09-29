"""Curation records: graph-only writes made to travel as vault files (spec
2026-09-26 §7, D6 -- the graph must be rebuildable from the vault alone).

A machine with its own Neo4j never sees a write that lives only in another
machine's graph. So every curation write (a detected conflict and its
resolution, in this module's first kind) is also a file:

    .artmind/data/curation/<kind>/<record id>.json

**One file per record, never one shared file.** Every path under
`.artmind/data/**` is `merge=binary` (spec R3), so a single `conflicts.yaml`
would turn any two machines' independent curation into a whole-file git
conflict that blocks `vault sync` -- the same lesson as the per-table
`.meta.json` that replaced `structured_text/manifest.json` (spec R5). With
one file per record, two machines only collide when they write the SAME
record.

A **kind** says how its records reach the graph (`Kind`); `vault sync`'s
curation track applies every added or changed record and removes every
deleted one, for every kind in `kinds()`. Files are written atomically (a
sibling temp file, then `os.replace`) so an Obsidian Git auto-commit never
captures half a record, and serialised deterministically (sorted keys) so an
unchanged record is byte-identical and never churns in git.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

#: The temp-file suffix `write_record` renames from. Shares
#: `atomic_dir.TMP_SUFFIX` so a leftover is recognisably artmind scratch; the
#: curation track reads only `*.json`, so a leftover is never applied.
TMP_SUFFIX = ".artmind-tmp"

_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


@dataclass(frozen=True)
class Kind:
    """How one kind of curation record reaches the graph.

    - `name`: its directory under `curation/`.
    - `phase`: `"pre"` -- applied before `vault sync`'s union projection
      rebuild, and the keys it returns join that rebuild -- or `"post"`,
      applied after it, because it needs the rebuilt entities (a conflict's
      `CONFLICTS_WITH` edges join two `:Entity` nodes).
    - `apply(tx, record, fingerprint)`: write one record, idempotently
      (MERGE), recording `fingerprint` on the graph; returns the aggregate
      keys whose projection it changed.
    - `remove(tx, record)`: undo one record whose file was deleted --
      `record` is the file as it last existed (at `vault sync`'s base), or
      just `{"id": ...}` when that cannot be read; returns keys the same way.
    - `read_fingerprints(ids, *, timeout)`: `{id: fingerprint}` for the ids
      the graph holds, in one query -- what lets a shared graph skip records
      another machine already applied, and `vault status` count only what is
      really pending.
    - `domains(record)`: the domains a record belongs to, for `--domain`.
    - `reapply_for(doc_ids)` (optional): ids of this kind's records to apply
      again after `vault sync` replays those documents -- for a kind whose
      effect a document replay would undo (a retirement).
    """

    name: str
    phase: str
    apply: Callable[[Any, dict, str], set]
    remove: Callable[[Any, dict], set]
    read_fingerprints: Callable[..., dict]
    domains: Callable[[dict], list]
    reapply_for: Callable[[list], list] | None = None


def kinds() -> dict[str, Kind]:
    """Every registered kind, by directory name, in apply order."""
    from artmind import conflict_records, lifecycle_records, supersession_records, synthesis_records

    return {
        kind.name: kind
        for kind in (lifecycle_records.KIND, supersession_records.KIND, synthesis_records.KIND, conflict_records.KIND)
    }


def curation_dir() -> Path:
    """`paths.CURATION_DIR`, resolved at call time so tests can repoint it."""
    import paths

    return Path(paths.CURATION_DIR)


def _check_id(record_id: str) -> str:
    if not isinstance(record_id, str) or not _SAFE_ID.fullmatch(record_id):
        raise ValueError(f"{record_id!r} is not a safe curation record id (letters, digits, '.', '_', '-')")
    return record_id


def record_path(kind: str, record_id: str) -> Path:
    return curation_dir() / kind / f"{_check_id(record_id)}.json"


def serialize(record: dict) -> bytes:
    """The one byte form of a record: sorted keys, two-space indent, UTF-8,
    one trailing newline. Deterministic, so rewriting an unchanged record
    changes nothing git can see."""
    return (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def fingerprint(data: bytes) -> str:
    """sha256 of a record file's bytes -- the working-tree file's when this
    machine writes it, the committed blob's when `vault sync` applies it."""
    return hashlib.sha256(data).hexdigest()


def write_record(kind: str, record: dict) -> str:
    """Write `record` to its file atomically and return its fingerprint.
    Bytes already on disk are left alone (no mtime churn for Obsidian Git)."""
    data = serialize(record)
    path = record_path(kind, record["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == data:
        return fingerprint(data)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return fingerprint(data)


def read_record(kind: str, record_id: str) -> dict | None:
    """The record as it stands in this working tree, or None."""
    path = record_path(kind, record_id)
    if not path.is_file():
        return None
    return parse(path.read_bytes())


def parse(data: bytes | None) -> dict | None:
    """A record from its bytes; None when absent or not a JSON object."""
    if data is None:
        return None
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def delete_record(kind: str, record_id: str) -> bool:
    """Remove a record's file (a plain delete: Obsidian Git records it).
    Returns whether there was one."""
    path = record_path(kind, record_id)
    if not path.is_file():
        return False
    path.unlink()
    return True
