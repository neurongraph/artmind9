"""`artmind vault resolve` (spec 2026-09-26 §5 R7): settle merge conflicts in
artmind's generated files, deterministically, so two machines resolving the
same conflict pick the same content.

Everything under `.artmind/data/**` is regenerable and `merge=binary` (R3):
git reports a conflict instead of line-merging two extraction runs into a
valid-looking mix. This command picks one WHOLE side per unit and stages it:

- **KG document folder** (`kg/<domain>/<folder>/`: document.json,
  observations.json, chunks.json, relationships.json and its chunk cache,
  all together, never mixed). The side whose `document.json`
  `content_sha256` equals the sha of the merged note's body wins; if the
  merged note was deleted, the side without the folder wins; otherwise the
  greater staging fingerprint (`sync_state.staging_fingerprint`) -- arbitrary
  but the same on every machine.
- **Chunk cache** (`documents/markdowns/<stem>_chunks/`: `chunk_NNN.md` and
  `chunks_meta.json`, together). Derived from the note's body, so it follows
  the note like the kg folder does: the side whose `chunks_meta.json`
  `_body_sha256` equals the sha of the merged note's body (the note named
  `<stem>.md`) wins; a cache with no stamp, a note that matches neither or
  both sides, or no single such note falls to the greater digest of the two
  folders, whole -- never a file from each. (`extract_kg` re-checks the
  cache against the note whichever way this went, so a wrong pick costs a
  re-split, not a wrong extraction.)
- **Structured table** (`structured_text/<domain>/<table>.csv` +
  `.meta.json` together): the side that has the table over the side that
  deleted it, then the later `table.ingested_at` (the table's last refresh),
  then the greater digest of the two files.
- **Curation record** (`curation/<kind>/<id>.json`, one file per record,
  spec §15 A9/A10): a kept record over a deleted one, then a per-kind rule
  (`_RECORD_RULES`), then the greater record fingerprint.
- **Any other file under `.artmind/data/`**: a kept file over a deleted one,
  then the greater sha256 of its bytes.

Every rule reads only the two sides' committed content and the merged note
-- never which side is "ours" -- so the machine that merged A into B and the
one that merged B into A choose the same bytes, and their merge commits then
merge cleanly with each other.

Reported, never touched: a human note (resolve it in Obsidian), and
artmind's human-curated files outside `.artmind/data/` (`same_as.yaml`,
domain schemas and table mappings, `vault.yaml`). A KG folder whose note is
still conflicted waits until the note is resolved (its body decides).

A path with a symlinked parent directory inside the vault is reported, never
touched (its unit is skipped): unlinking or staging through a symlink writes
outside the tree.

**The one git write artmind makes (spec D1/D2, as amended).** For each unit
it picked, `git checkout <HEAD|MERGE_HEAD> -- <paths>` for the files the
chosen side has, deletes those it lacks, then `git add -- <paths>`. Only
paths under `.artmind/data/`, only while a merge is in progress, and never a
commit: Obsidian Git (or you) concludes the merge. `--dryRun` shows each
pick and writes nothing.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from utils.functions import run_command

from artmind.vault import MARKER

#: Everything `vault resolve` may write, relative to the vault root.
DATA_PREFIX = f"{MARKER}/data/"

#: Pathspecs are literal: a file name holding `*` or `[` is one file.
_LITERAL = {"GIT_LITERAL_PATHSPECS": "1"}

_BATCH = 200

#: Gitignored, derived sidecars (`.gitignore` v3+): a folder holding nothing
#: but these is an empty shell once its tracked files are gone.
_SHELL_NAMES = frozenset({"embeddings.json"})

_EPOCH = datetime(1, 1, 1, tzinfo=timezone.utc)


def _when(value) -> datetime:
    """A record timestamp as a timezone-aware datetime, so `Z`, `+00:00`,
    fractions and other offsets compare as the instants they are (a naive
    stamp is UTC). Missing or unparsable sorts lowest."""
    if not isinstance(value, str):
        return _EPOCH
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return _EPOCH
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _has_conflict_block(text: str) -> bool:
    """Whether `text` holds a whole conflict block -- `<<<<<<<`, then
    `=======`, then `>>>>>>>` -- never a lone `=======` (a setext heading's
    underline, a horizontal rule, an ASCII table)."""
    stage = 0
    for line in text.splitlines():
        if stage == 0 and line.startswith("<<<<<<<"):
            stage = 1
        elif stage == 1 and line.rstrip() == "=======":
            stage = 2
        elif stage == 2 and line.startswith(">>>>>>>"):
            return True
    return False


def _note_text(note: Path) -> str:
    """A note's text as ingest reads it: `read_text` translates CRLF (and a
    lone CR) to LF, so the body hashed here is the body `content_sha256`
    was computed from at ingest. Hashing the raw bytes of a CRLF note would
    never match the staged `document.json`."""
    return note.read_text(encoding="utf-8", errors="replace")


class ResolveError(Exception):
    """`vault resolve` cannot run; nothing was written."""


def _read(vault_dir: Path, args: list[str]) -> str:
    rc, out, err = run_command(["git", *args], cwd=vault_dir, extra_env=_LITERAL)
    if rc != 0:
        raise ResolveError(f"git {' '.join(args[:3])} failed: {(err or out).strip()}")
    return out


def _merge_heads(vault_dir: Path) -> tuple[str, str]:
    """`(HEAD, MERGE_HEAD)` of the merge in progress; refuses otherwise."""
    from artmind.vault_sync import operation_in_progress

    what = operation_in_progress(vault_dir)
    if what is None:
        raise ResolveError("no merge is in progress in this vault -- nothing to resolve")
    if what != "a merge":
        raise ResolveError(f"{what} is in progress, not a merge -- `vault resolve` only settles merges (spec D5)")
    git_dir = Path(_read(vault_dir, ["rev-parse", "--absolute-git-dir"]).strip())
    heads = [line.strip() for line in (git_dir / "MERGE_HEAD").read_text().splitlines() if line.strip()]
    if len(heads) != 1:
        raise ResolveError("an octopus merge (several MERGE_HEADs) -- resolve it by hand")
    return _read(vault_dir, ["rev-parse", "HEAD"]).strip(), heads[0]


def _unmerged(vault_dir: Path) -> list[str]:
    out = _read(vault_dir, ["diff", "--name-only", "-z", "--diff-filter=U"])
    return sorted({p for p in out.split("\0") if p})


def _unit_of(path: str) -> tuple[str, str]:
    """`(kind, unit)` for a conflicted path under `.artmind/data/`."""
    parts = PurePosixPath(path[len(DATA_PREFIX):]).parts
    if len(parts) >= 4 and parts[0] == "kg":
        return "kg", f"{DATA_PREFIX}kg/{parts[1]}/{parts[2]}"
    if len(parts) >= 4 and parts[:2] == ("documents", "markdowns") and parts[2].endswith("_chunks"):
        return "chunks", f"{DATA_PREFIX}documents/markdowns/{parts[2]}"
    if len(parts) == 3 and parts[0] == "structured_text":
        for suffix in (".meta.json", ".csv"):
            if parts[2].endswith(suffix):
                return "table", f"{DATA_PREFIX}structured_text/{parts[1]}/{parts[2][:-len(suffix)]}"
    if len(parts) == 3 and parts[0] == "curation" and parts[2].endswith(".json"):
        return "record", path
    return "file", path


def _unit_paths(vault_dir: Path, kind: str, unit: str, heads: tuple[str, str]) -> list[str]:
    """Every file of the unit on either side."""
    if kind in ("kg", "chunks"):
        found: set[str] = set()
        for rev in heads:
            out = _read(vault_dir, ["ls-tree", "-r", "-z", "--name-only", rev, "--", unit])
            found.update(p for p in out.split("\0") if p)
        return sorted(found)
    if kind == "table":
        return [f"{unit}.csv", f"{unit}.meta.json"]
    return [unit]


def _side_files(vault_dir: Path, rev: str, paths: list[str]) -> dict[str, bytes]:
    """`{path: bytes}` for the unit's files present at `rev`."""
    from artmind.vault_sync import _cat_blobs

    out = _read(vault_dir, ["ls-tree", "-r", "-z", rev, "--", *paths])
    oid_of = {}
    for entry in out.split("\0"):
        meta, _, path = entry.partition("\t")
        fields = meta.split(" ")
        if path in paths and len(fields) == 3 and fields[1] == "blob":
            oid_of[path] = fields[2]
    blobs = _cat_blobs(vault_dir, list(oid_of.values()))
    return {path: blobs[oid] for path, oid in oid_of.items() if oid in blobs}


def _json(data: bytes | None) -> dict | None:
    if data is None:
        return None
    try:
        obj = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _digest(files: dict[str, bytes], paths: list[str]) -> str:
    """A deterministic digest of one side of a unit: each path and its bytes,
    in path order; empty when the side has none of them."""
    if not files:
        return ""
    h = hashlib.sha256()
    for path in paths:
        data = files.get(path)
        h.update(path.encode("utf-8") + b"\0" + (b"absent" if data is None else hashlib.sha256(data).digest()))
    return h.hexdigest()


def _greater(keys: dict[str, tuple]) -> str:
    """The side whose key is greater; `ours` on a tie (the sides are then
    identical in everything the rule reads)."""
    return "theirs" if keys["theirs"] > keys["ours"] else "ours"


# ── per-unit rules ───────────────────────────────────────────────────────────


def _note_state(
    vault_dir: Path, documents: dict, ids: "_NoteIndex", unmerged: frozenset = frozenset()
) -> tuple[str, str | None]:
    """`("body", sha)` for the merged note's body, `("deleted", path)` when
    the merge removed it, `("conflicted", path)` while git still lists it
    unmerged AND its working-tree file still holds a conflict block, or
    `("unknown", None)` when no note can be found (a converted binary, an
    `artmind update` folder). A document from before `source_path` whose note
    the merge deleted is found by its `_artmind_id` in the two merged heads'
    notes. The working tree is what counts: a note you resolved in Obsidian
    but have not staged yet is resolved.

    `documents` is walked in the order given, and the first note found
    decides: callers order it by content, never by side."""
    from artmind.document_identity import compute_content_sha256
    from artmind.ingest import _parse_md_frontmatter

    candidates = []
    for document in documents.values():
        if not document:
            continue
        source = document.get("source_path")
        if (
            isinstance(source, str) and document.get("source_type") in (None, "md")
            and not PurePosixPath(source).is_absolute() and not source.startswith(MARKER)
        ):
            candidates.append(source)
        found = ids.path_of(document.get("id")) or ids.historic_path(document.get("id"))
        if found:
            candidates.append(found)
    for relpath in dict.fromkeys(candidates):
        note = vault_dir / relpath
        if not note.is_file():
            continue
        text = _note_text(note)
        if relpath in unmerged and _has_conflict_block(text):
            return "conflicted", relpath
        _, body = _parse_md_frontmatter(text)
        return "body", compute_content_sha256(body)
    if candidates:
        return "deleted", candidates[0]
    return "unknown", None


class _NoteIndex:
    """`_artmind_id -> vault-relative note path`, read from the working
    tree's notes once, on first use."""

    def __init__(self, vault_dir: Path, heads: tuple[str, ...] = ()):
        self._vault_dir = vault_dir
        self._heads = heads
        self._paths: dict[str, str] | None = None
        self._stems: dict[str, list[str]] | None = None

    def _load(self) -> None:
        if self._paths is not None:
            return
        from artmind.ingest import _parse_md_frontmatter
        from artmind.reindex import _iter_vault_markdown

        self._paths, self._stems = {}, {}
        for note in _iter_vault_markdown(self._vault_dir):
            relpath = note.relative_to(self._vault_dir).as_posix()
            self._stems.setdefault(note.stem, []).append(relpath)
            try:
                meta, _ = _parse_md_frontmatter(_note_text(note))
            except (OSError, UnicodeDecodeError):
                continue
            if isinstance(meta.get("_artmind_id"), str):
                self._paths.setdefault(meta["_artmind_id"], relpath)

    def path_of(self, artmind_id) -> str | None:
        if not isinstance(artmind_id, str):
            return None
        self._load()
        return self._paths.get(artmind_id)

    def paths_named(self, stem: str) -> list[str]:
        """Vault-relative paths of the working tree's notes named `stem.md`."""
        self._load()
        return list(self._stems.get(stem, ()))

    def historic_path(self, artmind_id) -> str | None:
        """A note path at either merged head whose frontmatter names
        `artmind_id` -- the note a merge deleted, for a document whose
        `document.json` does not name its `source_path`. Asks git only when
        the working tree has no such note."""
        if not isinstance(artmind_id, str) or not artmind_id:
            return None
        from artmind.ingest import _parse_md_frontmatter

        for rev in self._heads:
            rc, out, _ = run_command(
                ["git", "grep", "-lFIz", "-e", artmind_id, rev], cwd=self._vault_dir,
                extra_env=_LITERAL, expected_codes=(1,),
            )
            if rc != 0:
                continue
            for entry in sorted(out.split("\0")):
                path = entry.partition(":")[2] if entry.startswith(f"{rev}:") else ""
                if not path.endswith(".md") or path.startswith(f"{MARKER}/"):
                    continue
                meta, _ = _parse_md_frontmatter(_read(self._vault_dir, ["show", f"{rev}:{path}"]))
                if meta.get("_artmind_id") == artmind_id:
                    return path
        return None


def _decide_kg(vault_dir, unit, paths, sides, ids, unmerged=frozenset()) -> tuple[str | None, str]:
    from artmind.sync_state import staging_fingerprint

    digests = {s: _digest(files, paths) for s, files in sides.items()}
    # Content order, not ours/theirs: two sides naming different notes must
    # resolve to the same note whichever machine merged which.
    documents = {
        s: _json(sides[s].get(f"{unit}/document.json")) for s in sorted(sides, key=lambda s: digests[s])
    }
    state, value = _note_state(vault_dir, documents, ids, unmerged)
    if state == "conflicted":
        return None, f"its note {value} is still conflicted -- resolve the note, then re-run `vault resolve`"
    if state == "body":
        matching = [s for s, d in documents.items() if d and d.get("content_sha256") == value]
        if len(matching) == 1:
            return matching[0], "the side extracted from the merged note's body"
    if state == "deleted":
        absent = [s for s, files in sides.items() if not files]
        if len(absent) == 1:
            return absent[0], f"the merge deleted its note {value}"
    fingerprints = {
        s: (staging_fingerprint(lambda name, f=files: f.get(f"{unit}/{name}")) or "", digests[s])
        for s, files in sides.items()
    }
    return _greater(fingerprints), "the greater staging fingerprint (both or neither match the note)"


def _decide_chunks(vault_dir, unit, paths, sides, ids, unmerged=frozenset()) -> tuple[str | None, str]:
    """A chunk cache is split from one note's body and stamped with its hash
    (`_persist_chunks`): the side whose stamp is the merged note's body hash
    wins, whole. Anything else (no stamp, no single note named for the folder,
    a note matching neither or both) is the greater digest -- deterministic,
    and `extract_kg` re-checks the cache against the note anyway."""
    from artmind.document_identity import compute_content_sha256
    from artmind.ingest import CHUNK_CACHE_BODY_KEY, _parse_md_frontmatter

    stem = PurePosixPath(unit).name.removesuffix("_chunks")
    notes = ids.paths_named(stem)
    if len(notes) == 1 and (vault_dir / notes[0]).is_file():
        text = _note_text(vault_dir / notes[0])
        if notes[0] in unmerged and _has_conflict_block(text):
            return None, f"its note {notes[0]} is still conflicted -- resolve the note, then re-run `vault resolve`"
        body_sha = compute_content_sha256(_parse_md_frontmatter(text)[1])
        matching = [
            s for s, files in sides.items()
            if (_json(files.get(f"{unit}/chunks_meta.json")) or {}).get(CHUNK_CACHE_BODY_KEY) == body_sha
        ]
        if len(matching) == 1:
            return matching[0], "the cache split from the merged note's body"
    digests = {s: _digest(files, paths) for s, files in sides.items()}
    return _greater({s: (d,) for s, d in digests.items()}), "the greater digest (no cache stamp names the merged note's body)"


def _decide_table(unit, paths, sides) -> tuple[str, str]:
    present = [s for s, files in sides.items() if files]
    if len(present) == 1:
        return present[0], "the side that kept the table"

    def _key(files):
        meta = _json(files.get(f"{unit}.meta.json")) or {}
        table = meta.get("table") if isinstance(meta.get("table"), dict) else {}
        return (_when(table.get("ingested_at")), _digest(files, paths))

    return _greater({s: _key(files) for s, files in sides.items()}), "the later refresh (table.ingested_at), then the greater digest"


def _conflict_key(record: dict) -> tuple:
    """A decision beats an open conflict; then the later last change
    (`resolved_at`, else `detected_at`)."""
    decided = (record.get("status") or "open") != "open"
    return (decided, _when(record.get("resolved_at") or record.get("detected_at")))


def _supersession_key(record: dict) -> tuple:
    """A human's assertion (`ingest supersede`) beats a detector's."""
    return (record.get("detected_by") == "manual",)


def _synthesis_key(record: dict) -> tuple:
    """The later synthesis (it read the later observation set)."""
    return (_when(record.get("created_at")),)


#: Per kind: a key over the parsed record; greater wins, then the greater
#: record fingerprint. A kind with no entry (lifecycle: its content is fixed
#: by its id) goes straight to the fingerprint.
_RECORD_RULES = {
    "conflicts": (_conflict_key, "a decision over open, then the later change"),
    "supersessions": (_supersession_key, "a manual assertion over a detected one"),
    "syntheses": (_synthesis_key, "the later synthesis"),
}


def _decide_record(unit, sides) -> tuple[str, str]:
    from artmind import curation_records

    present = [s for s, files in sides.items() if files]
    if len(present) == 1:
        return present[0], "the side that kept the record"
    kind = PurePosixPath(unit).parts[-2]
    key_fn, why = _RECORD_RULES.get(kind, (lambda record: (), "identical rule keys"))
    keys = {}
    for s, files in sides.items():
        data = files[unit]
        keys[s] = (key_fn(_json(data) or {}), curation_records.fingerprint(data))
    return _greater(keys), f"{why}, then the greater record fingerprint"


def _decide_file(unit, sides) -> tuple[str, str]:
    present = [s for s, files in sides.items() if files]
    if len(present) == 1:
        return present[0], "the side that kept the file"
    return (
        _greater({s: (hashlib.sha256(files[unit]).hexdigest(),) for s, files in sides.items()}),
        "the greater content hash",
    )


# ── plan and apply ───────────────────────────────────────────────────────────


def _reason_for_human(path: str) -> str:
    if path.startswith(f"{MARKER}/"):
        return "human-curated (same_as.yaml, schemas, table mappings, vault.yaml) -- resolve it by hand"
    return "your note -- resolve it in Obsidian"


def _symlinked_parent(vault_dir: Path, path: str) -> str | None:
    """The first directory above `path` that is a symlink in the working
    tree (vault-relative), or None. Deleting, checking out or staging through
    one reaches outside the vault."""
    current = vault_dir
    for part in PurePosixPath(path).parts[:-1]:
        current = current / part
        if current.is_symlink():
            return current.relative_to(vault_dir).as_posix()
    return None


def plan(vault_dir: Path) -> dict:
    """Every conflicted path, sorted into resolved units (with the side each
    takes and why), pending units, and reported paths. Reads only."""
    vault_dir = Path(vault_dir)
    ours, theirs = _merge_heads(vault_dir)
    conflicted = _unmerged(vault_dir)
    ids = _NoteIndex(vault_dir, (ours, theirs))
    unmerged = frozenset(conflicted)

    units: dict[str, str] = {}
    reported = []
    for path in conflicted:
        if path.startswith(DATA_PREFIX):
            kind, unit = _unit_of(path)
            units.setdefault(unit, kind)
        else:
            reported.append({"path": path, "why": _reason_for_human(path)})

    resolved, pending = [], []
    for unit, kind in sorted(units.items()):
        paths = _unit_paths(vault_dir, kind, unit, (ours, theirs))
        sides = {"ours": _side_files(vault_dir, ours, paths), "theirs": _side_files(vault_dir, theirs, paths)}
        linked = {p: _symlinked_parent(vault_dir, p) for p in paths}
        if any(linked.values()):
            reported.extend(
                {"path": p, "why": f"its parent {parent} is a symlink -- resolve it by hand, artmind never writes through one"}
                for p, parent in linked.items() if parent
            )
            continue
        if kind == "kg":
            side, why = _decide_kg(vault_dir, unit, paths, sides, ids, unmerged)
        elif kind == "chunks":
            side, why = _decide_chunks(vault_dir, unit, paths, sides, ids, unmerged)
        elif kind == "table":
            side, why = _decide_table(unit, paths, sides)
        elif kind == "record":
            side, why = _decide_record(unit, sides)
        else:
            side, why = _decide_file(unit, sides)
        entry = {"unit": unit, "kind": kind, "paths": paths}
        if side is None:
            pending.append({**entry, "why": why})
        else:
            resolved.append({**entry, "side": side, "rule": why})
    return {
        "head": ours, "merge_head": theirs,
        "resolved": resolved, "pending": pending, "reported": reported,
        "remaining": len(pending) + len(reported),
    }


def _assert_scoped(paths: list[str], vault_dir: Path | None = None) -> None:
    """The write guard: every path is under `.artmind/data/`, relative, with
    no `..` and (given `vault_dir`) no symlinked parent -- checked before any
    git write, whatever the planner produced."""
    for path in paths:
        if vault_dir is not None and _symlinked_parent(vault_dir, path):
            raise ResolveError(f"refusing to write {path!r}: a parent directory is a symlink")
        parts = PurePosixPath(path).parts
        if not path.startswith(DATA_PREFIX) or PurePosixPath(path).is_absolute() or ".." in parts:
            raise ResolveError(f"refusing to write {path!r}: `vault resolve` only writes under {DATA_PREFIX}")


def _batches(paths: list[str]):
    for i in range(0, len(paths), _BATCH):
        yield paths[i:i + _BATCH]


def _prune_empty_dirs(vault_dir: Path, removed: list[str]) -> None:
    """Remove the directories deleting `removed` emptied, up to (never
    including) `.artmind/data/` -- a folder the chosen side does not have
    leaves no empty shell for a folder scan to trip over. A directory holding
    nothing but a gitignored derived sidecar (`embeddings.json`, a file or an
    empty directory) is a shell too, and goes with it."""
    stop = vault_dir / DATA_PREFIX
    for directory in sorted({(vault_dir / p).parent for p in removed}, key=lambda d: len(d.parts), reverse=True):
        while directory != stop and stop in directory.parents:
            try:
                leftovers = list(directory.iterdir())
                if not all(entry.name in _SHELL_NAMES for entry in leftovers):
                    break
                for entry in leftovers:
                    entry.rmdir() if entry.is_dir() and not entry.is_symlink() else entry.unlink()
                directory.rmdir()
            except OSError:
                break
            directory = directory.parent


def _checkout(vault_dir: Path, rev: str, batch: list[str]) -> None:
    rc, out, err = run_command(["git", "checkout", rev, "--", *batch], cwd=vault_dir, extra_env=_LITERAL)
    if rc != 0:
        raise ResolveError(f"checking out {rev[:12]} failed: {(err or out).strip()}")


def _add(vault_dir: Path, batch: list[str]) -> None:
    rc, out, err = run_command(["git", "add", "--", *batch], cwd=vault_dir, extra_env=_LITERAL)
    if rc != 0:
        raise ResolveError(f"staging failed: {(err or out).strip()}")


def _stage_side(vault_dir: Path, rev: str, present: list[str], absent: list[str]) -> None:
    """Make the index and working tree hold `rev`'s version of the unit:
    delete the files it lacks from the working tree, check out the ones it
    has, stage the lot.

    Resumable: a rerun after any interruption finishes the unit. `plan` finds
    a unit by its unmerged paths, so the paths git still lists as unmerged are
    settled LAST -- while any is left the unit is still found and every step
    here is idempotent; the moment none is left, every other path is done.
    The working-tree deletions come first because they touch no index entry."""
    _assert_scoped(present + absent, vault_dir)
    unmerged = set(_unmerged(vault_dir))
    for path in absent:
        (vault_dir / path).unlink(missing_ok=True)
    _prune_empty_dirs(vault_dir, absent)
    tracked = set()
    if absent:
        listed = _read(vault_dir, ["ls-files", "-z", "--", *absent])
        tracked = {p for p in listed.split("\0") if p}
    for settle_unmerged in (False, True):
        keep = [p for p in present if (p in unmerged) == settle_unmerged]
        gone = [p for p in absent if p in tracked and (p in unmerged) == settle_unmerged]
        for batch in _batches(keep):
            _checkout(vault_dir, rev, batch)
        for batch in _batches(keep + gone):
            _add(vault_dir, batch)


def resolve(vault_dir: Path, *, dry_run: bool = False) -> dict:
    """Plan, then (unless `dry_run`) stage the chosen side of every resolved
    unit. Never commits."""
    vault_dir = Path(vault_dir)
    report = plan(vault_dir)
    report["dry_run"] = dry_run
    if dry_run:
        return report
    from artmind.vault_sync import worker_running

    if worker_running(vault_dir):
        raise ResolveError(
            "the ingest worker is running for this vault -- wait for it to finish "
            "(`artmind ingest job-status`), then re-run `vault resolve`"
        )
    heads = {"ours": report["head"], "theirs": report["merge_head"]}
    for unit in report["resolved"]:
        _assert_scoped(unit["paths"], vault_dir)
    for unit in report["resolved"]:
        if _merge_heads(vault_dir) != (report["head"], report["merge_head"]):
            raise ResolveError("the merge changed underneath `vault resolve` -- re-run it")
        rev = heads[unit["side"]]
        have = set(_side_files(vault_dir, rev, unit["paths"]))
        present = [p for p in unit["paths"] if p in have]
        absent = [p for p in unit["paths"] if p not in have]
        _stage_side(vault_dir, rev, present, absent)
    return report
