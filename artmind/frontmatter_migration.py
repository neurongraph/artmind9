"""`artmind vault migrate-frontmatter` (spec 2026-09-26 §5 R6, §12 item 5).

Before R6 every ingest rewrote a note's frontmatter with `_version`,
`_content_sha256`, `_source_commit`, `_ingested_at` and friends -- so nearly
every note conflict between two machines had artmind's lines in it. Those
fields now live in the note's staging folder (`document.json`); this command
moves them there for every note that still carries them, and rewrites each
such note once, removing them. It is the one bulk note rewrite, run
deliberately, with Obsidian Git's auto-commit paused.

Per note, in this order:

1. copy each moved field into `document.json` **only where that key is
   absent** -- a value a newer ingest already recorded always wins;
   `content_sha256`, `source_sha256`, `source_commit` and `ingested_at`
   describe one ingest, so they are copied only when the note's `_version`
   equals the staged `version` (otherwise the extraction they describe never
   landed, and leaving them out makes the next ingest re-extract, which is
   what that note needs);
2. rewrite the note without the moved fields (`artmind.frontmatter`: body,
   the user's keys and their order, and line endings kept byte-for-byte).

Idempotent: a note with none of the fields is left alone. Resumable: an
interruption between 1 and 2 leaves `document.json` already holding what
step 1 would copy again, and the rerun finishes step 2. A note with no
staging folder (never extracted) keeps its fields -- they still work as the
legacy baseline -- and is reported. Only the note's frontmatter and
`document.json` are written; nothing touches git or the graph.

A note is left exactly as it was, and listed under `skipped` with the reason,
when: its frontmatter is unparsable, not a mapping, or behind a BOM; its
`_artmind_id`/`_domain` are missing, not strings, or `_domain` is not a plain
folder name; a value to move is not a plain scalar; it is a symlink out of the
vault; its `_artmind_id` is shared by several notes (an Obsidian copy) and none
is named after the staging folder -- the note that is named after it migrates
either way; or anything unexpected goes wrong. One bad note never stops the run.
"""
from __future__ import annotations

import collections
import json
import math
import os
from pathlib import Path

from artmind.document_identity import MOVED_FIELDS

#: Printed before anything else, dry run or not.
PAUSE_NOTICE = (
    "Before migrating: pause Obsidian Git's automatic commit-and-sync on this machine "
    "(Obsidian -> Settings -> Git: set the auto commit-and-sync interval to 0, or disable "
    "the plugin) and let every other machine finish pushing. This rewrites the frontmatter "
    "of every note that still carries artmind's per-ingest fields. When it finishes, "
    "commit the result as one change, then turn auto commit-and-sync back on."
)

#: Fields that describe one particular ingest: meaningful in document.json
#: only when that ingest's extraction is the one staged there.
_VERSION_BOUND = frozenset({"content_sha256", "source_sha256", "source_commit", "ingested_at"})


class MigrationError(Exception):
    """The vault is in a state the migration must not run in; nothing was
    written."""


def _plain(value):
    """YAML may hand back a date/datetime for an unquoted timestamp."""
    return value.isoformat() if hasattr(value, "isoformat") else value


def _plain_name(value) -> bool:
    """`value` is one plain directory name: it may be a path component of the
    KG dir, so nothing that could leave it (`/`, `..`, an absolute path)."""
    return (
        isinstance(value, str) and value not in ("", ".")
        and "/" not in value and "\\" not in value and ".." not in value
        and not os.path.isabs(value)
    )


def _json_scalar(value) -> bool:
    """A value document.json can hold as itself: str/int/float/bool/None (a
    YAML `!!binary`, list or mapping is not something to guess a form for)."""
    if isinstance(value, float):
        return math.isfinite(value)
    return value is None or isinstance(value, (str, int, bool))


def _notes(vault_dir: Path) -> list[Path]:
    """Every vault note (the walk `docs reindex` uses) and every converted
    binary's markdown in the data dir's markdown store."""
    import paths
    from artmind.reindex import _iter_vault_markdown

    notes = list(_iter_vault_markdown(vault_dir))
    markdowns = Path(paths.MARKDOWNS_DIR)
    if markdowns.is_dir():
        notes += sorted(markdowns.glob("*.md"))
    return notes


def _read_note(path: Path) -> tuple[str, dict]:
    """`(raw text, frontmatter mapping)`. Raises `OSError`/`UnicodeDecodeError`
    (unreadable) or `FrontmatterEditError` (frontmatter that is not a YAML
    mapping, or sits behind a BOM) -- unlike `ingest._parse_md_frontmatter`,
    which reads all of those as "no frontmatter", and so would let a note
    carrying artmind's fields pass as already minimal."""
    from artmind import frontmatter

    raw = frontmatter.read_note(path)
    if raw.startswith("\ufeff") and frontmatter.has_frontmatter(raw[1:]):
        frontmatter._refuse_bom(raw)
    split = frontmatter._split(raw)
    if split is None:
        return raw, {}
    return raw, frontmatter._load(split[1])


def _id_counts(notes: list[Path]) -> collections.Counter:
    """How many notes carry each `_artmind_id` -- an Obsidian copy of an
    ingested note carries its original's."""
    counts: collections.Counter = collections.Counter()
    for path in notes:
        try:
            artmind_id = _read_note(path)[1].get("_artmind_id")
        except Exception:
            continue
        if isinstance(artmind_id, str) and artmind_id:
            counts[artmind_id] += 1
    return counts


class _StagingIndex:
    """`(domain, artmind_id) -> staging folder`: the folder named after the
    note first, else -- `by_id` -- any folder in the domain whose document.json
    carries the id (a note renamed since its extraction). Each domain is read
    once."""

    def __init__(self, kg_dir: Path):
        self._kg_dir = Path(kg_dir)
        self._by_domain: dict[str, dict[str, Path]] = {}

    @staticmethod
    def _read(folder: Path) -> dict | None:
        try:
            document = json.loads((folder / "document.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return document if isinstance(document, dict) else None

    def by_stem(self, domain: str, stem: str, artmind_id: str) -> tuple[Path, dict] | None:
        if not (_plain_name(domain) and _plain_name(stem)):
            return None
        folder = self._kg_dir / domain / stem
        document = self._read(folder)
        if document is not None and document.get("id") == artmind_id:
            return folder, document
        return None

    def by_id(self, domain: str, artmind_id: str) -> tuple[Path, dict] | None:
        if not _plain_name(domain):
            return None
        if domain not in self._by_domain:
            index: dict[str, Path] = {}
            domain_dir = self._kg_dir / domain
            if domain_dir.is_dir():
                for candidate in sorted(p for p in domain_dir.iterdir() if p.is_dir()):
                    if candidate.name.endswith((".artmind-tmp", ".artmind-old")):
                        continue
                    found = self._read(candidate)
                    if found is not None and isinstance(found.get("id"), str):
                        index.setdefault(found["id"], candidate)
            self._by_domain[domain] = index
        folder = self._by_domain[domain].get(artmind_id)
        if folder is None:
            return None
        document = self._read(folder)
        return (folder, document) if document is not None else None


def _skip(entry: dict, reason: str) -> dict:
    return {**entry, "action": "skip", "reason": reason}


def _plan(path: Path, index: _StagingIndex, id_counts: collections.Counter, vault_dir: Path) -> dict:
    """What migrating one note does, without doing it."""
    from artmind import frontmatter

    entry: dict = {"path": path}
    try:
        raw, meta = _read_note(path)
    except (OSError, UnicodeDecodeError) as e:
        return _skip(entry, f"unreadable: {e}")
    except frontmatter.FrontmatterEditError as e:
        return _skip(entry, f"frontmatter not readable, left untouched: {e}")
    moved = [key for key in MOVED_FIELDS if key in meta]
    if not moved:
        return {**entry, "action": "minimal"}
    artmind_id, domain = meta.get("_artmind_id"), meta.get("_domain")
    if not artmind_id or not domain:
        return _skip(entry, "no _artmind_id/_domain -- not an artmind note")
    if not isinstance(artmind_id, str) or not isinstance(domain, str):
        return _skip(entry, "_artmind_id and _domain must be strings")
    if not _plain_name(domain):
        return _skip(entry, f"_domain {domain!r} is not a plain folder name")
    if path.is_symlink():
        import paths

        real = Path(os.path.realpath(path))
        roots = [Path(os.path.realpath(r)) for r in (vault_dir, paths.MARKDOWNS_DIR)]
        if not any(real.is_relative_to(r) for r in roots):
            return _skip(entry, f"a symlink to {real}, outside the vault -- left untouched")
    found = index.by_stem(domain, path.stem, artmind_id)
    if found is None:
        found = index.by_id(domain, artmind_id)
        if found is not None and id_counts[artmind_id] > 1:
            return _skip(
                entry,
                f"duplicate _artmind_id: {id_counts[artmind_id]} notes carry it and none is named after "
                "the staging folder -- delete or re-id the copy, then re-run",
            )
    if found is None:
        return _skip(
            entry, "no staging folder (never extracted) -- its fields stay; they still work",
        )
    folder, document = found
    try:
        stripped = frontmatter.remove_fields(raw, moved)
    except frontmatter.FrontmatterEditError as e:
        return _skip(entry, str(e))
    version, staged_version = meta.get("_version"), document.get("version")
    same_version = version is not None and staged_version is not None and str(version) == str(staged_version)
    updates = {}
    for key in moved:
        target = MOVED_FIELDS[key]
        if target is None or target == "version" or target in document:
            continue
        if target in _VERSION_BOUND and not same_version:
            continue
        value = _plain(meta[key])
        if not _json_scalar(value):
            return _skip(entry, f"{key} holds a {type(value).__name__}, not a plain value -- move it by hand")
        updates[target] = value
    return {
        **entry, "action": "migrate", "raw": raw, "stripped": stripped, "moved": moved,
        "staging": folder, "document": document, "updates": updates,
        "version_mismatch": not same_version,
    }


def migrate(vault_dir: Path, *, dry_run: bool = False) -> dict:
    """Move every note's per-ingest frontmatter into its `document.json` and
    strip it from the note. Raises `MigrationError`, writing nothing, while a
    merge/rebase is in progress, the ingest worker runs or the vault is not a
    git repo. A note that cannot be migrated -- for whatever reason, an
    unexpected error included -- is listed under `skipped` and left as it was;
    the rest still migrate."""
    import paths
    from artmind import frontmatter, vault_sync
    from artmind.ingest import write_staging
    from artmind.vault import note_scratch_dir

    vault_dir = Path(vault_dir)
    try:
        what = vault_sync.operation_in_progress(vault_dir)
    except vault_sync.VaultSyncError as e:
        raise MigrationError(f"could not read this vault's git state (is it a git repository?): {e}") from e
    if what is not None:
        raise MigrationError(f"{what} is in progress in this vault -- finish it first, then re-run")
    if vault_sync.worker_running(vault_dir):
        raise MigrationError(
            "the ingest worker is running for this vault -- wait for it to finish "
            "(`artmind ingest job-status`), then re-run"
        )

    def _rel(path: Path) -> str:
        try:
            return path.relative_to(vault_dir).as_posix()
        except ValueError:
            return str(path)

    index = _StagingIndex(paths.KG_DIR)
    scratch = note_scratch_dir(vault_dir)
    report: dict = {
        "dry_run": dry_run, "notes": 0, "migrated": [], "already_minimal": 0,
        "document_json_updated": 0, "version_mismatch": [], "skipped": [],
    }
    notes = _notes(vault_dir)
    id_counts = _id_counts(notes)
    for path in notes:
        report["notes"] += 1
        try:
            plan = _plan(path, index, id_counts, vault_dir)
            if plan["action"] == "migrate" and not dry_run:
                # document.json first, the note second: a failure between the two
                # leaves the note whole and document.json holding only keys that
                # were absent -- the rerun copies nothing new and finishes the note.
                if plan["updates"]:
                    write_staging(plan["staging"], {"document.json": {**plan["document"], **plan["updates"]}})
                frontmatter.write_note(path, plan["stripped"], scratch_dir=scratch)
        except Exception as e:
            report["skipped"].append({"path": _rel(path), "reason": f"unexpected error, left untouched: {e!r}"})
            continue
        if plan["action"] == "minimal":
            report["already_minimal"] += 1
            continue
        if plan["action"] == "skip":
            report["skipped"].append({"path": _rel(path), "reason": plan["reason"]})
            continue
        if plan["version_mismatch"]:
            report["version_mismatch"].append(_rel(path))
        if plan["updates"]:
            report["document_json_updated"] += 1
        report["migrated"].append({
            "path": _rel(path), "removed": plan["moved"],
            "document_json": {"folder": _rel(plan["staging"]), "added": sorted(plan["updates"])},
        })
    return report
