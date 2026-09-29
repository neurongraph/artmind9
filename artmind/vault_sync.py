"""`vault sync`: git-diff-driven, CDC-style replay into Neo4j and the
structured store (docs/superpowers/specs/2026-09-25-vault-sync-design.md).

Detects exactly which committed KG-staging document folders
(`.artmind/data/kg/**`), committed structured-store text
(`.artmind/data/structured_text/**`), table mappings and domain schemas
(`.artmind/domains/**`) changed since the last sync, and replays only those
changes -- incremental and git-native, complementing (not replacing) the
whole-graph `session close`/`session initiate` snapshot.

Applies committed content only: every input -- mappings and schemas included
-- is read from the fixed `head` (`git show`/`git ls-tree -z`/`git archive`),
never from the working tree, and `sync` never commits (spec
2026-09-26-vault-git-transport-design.md, §6 A1, D1, §14).

Each store has its own bookmark (§6 A2): the graph's in the graph
(`artmind.sync_state`), the structured store's in `state.json`. A document
whose committed fingerprint the graph already carries is skipped (§6 A3),
and `pending_work`/`query_staleness_warning` report what is still to apply
(§6 A4). artmind never writes to this vault's git (D2) -- only reads it.

Curation travels too (spec §7, §15 A9): `artmind update` folders
(`kg/<domain>/update__*`) replay with track A; track C applies curation
records (`.artmind/data/curation/<kind>/<id>.json`, `artmind.curation_records`)
and track D rebuilds the members of every same-as group changed in
`.artmind/same_as.yaml`, with the groups as committed at `head`.
"""
from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
from pathlib import Path

from loguru import logger

from utils.functions import run_command

from artmind.vault import MARKER

#: The git object id of the canonical empty tree, present in every
#: repository -- `git diff <this> HEAD` is the standard way to diff "against
#: nothing", which is exactly `--bootstrapEmpty`'s "everything currently
#: committed counts as added" semantics, without special-casing a repo's own
#: (possibly multiple) root commit(s).
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

#: `structured.text_export.META_SUFFIX`, duplicated so classifying a diff
#: never imports DuckDB. test_vault_sync pins the two equal.
_META_SUFFIX = ".meta.json"


class VaultSyncError(Exception):
    """A `vault sync` run cannot proceed -- refused before writing anything."""


def _git(vault_dir: Path, args: list[str]) -> str:
    rc, out, err = run_command(["git", *args], cwd=vault_dir)
    if rc != 0:
        raise VaultSyncError(f"git {' '.join(args)} failed: {err or out}")
    return out


def head_sha(vault_dir: Path) -> str:
    """The vault's current commit sha. Raises a clear, specific
    `VaultSyncError` (not `_git`'s generic wrapper) when there are no
    commits yet -- exactly the state `artmind init` leaves a fresh vault in
    (it runs `git init`, never a commit), and the one case every `sync()`
    call needs a real, named message for, since bootstrap or not, dry-run or
    not, there is nothing to sync/stamp without a HEAD to point at."""
    rc, out, err = run_command(["git", "rev-parse", "HEAD"], cwd=vault_dir, expected_codes=(128,))
    if rc != 0:
        raise VaultSyncError(
            "this vault's git repo has no commits yet -- nothing to sync. "
            "Commit something to it first, then run `vault sync` again."
        )
    return out.strip()


def operation_in_progress(vault_dir: Path) -> str | None:
    """`"a merge"`, `"a rebase"` or `"a cherry-pick"` when one is in progress
    in the vault's repo, else None."""
    git_dir = Path(_git(vault_dir, ["rev-parse", "--absolute-git-dir"]).strip())
    for marker, what in (
        ("MERGE_HEAD", "a merge"),
        ("rebase-merge", "a rebase"),
        ("rebase-apply", "a rebase"),
        ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ):
        if (git_dir / marker).exists():
            return what
    return None


def unresolved_artmind_conflicts(vault_dir: Path) -> list[str]:
    """Paths under `.artmind/` with unresolved conflicts. Only artmind's own
    files (spec §14 A7): a conflicted human note does not change what sync
    applies, and Obsidian shows it to the user. `-z` keeps a name with a
    space (or a non-ASCII byte) whole (§14 A5)."""
    out = _git(vault_dir, ["diff", "--name-only", "-z", "--diff-filter=U", "--", MARKER])
    return [p for p in out.split("\0") if p]


def preflight(vault_dir: Path) -> None:
    """Refuse -- before reading or writing anything -- when the vault is in a
    state `sync` must not apply from (spec 2026-09-26 §6 A5, §14 A7): a merge,
    rebase or cherry-pick in progress, unresolved conflicts under `.artmind/`,
    or a running ingest worker. Each message says what to do next."""
    what = operation_in_progress(vault_dir)
    if what is not None:
        raise VaultSyncError(
            f"{what} is in progress in this vault -- finish it in Obsidian "
            "(or git) first, then re-run `vault sync`"
        )
    unmerged = unresolved_artmind_conflicts(vault_dir)
    if unmerged:
        raise VaultSyncError(
            f"unresolved conflicts in {len(unmerged)} file(s) under {MARKER}/ ({', '.join(unmerged[:5])}"
            f"{', ...' if len(unmerged) > 5 else ''}) -- resolve them first"
        )
    from artmind.vault import VaultLayout
    from artmind.worker_pid import live_pid
    import paths

    # The worker writes the same graph keys `sync` replays, and its staging
    # writes are what `sync` would be reading. Checked at every location a
    # live worker for this vault could plausibly hold (re-review item 2):
    #  - this vault's own pid file (item 1's fix; the common case)
    #  - paths.WORKER_PID_FILE: the process-wide location, which differs
    #    from the vault's own when ARTMIND_HOME is explicitly pointed
    #    elsewhere (a supported setup) -- the worker for THIS vault then
    #    writes there, not under vault_dir/.artmind/
    #  - paths.DATA_DIR / "worker.pid": the legacy (pre-item-1) location --
    #    a worker started before that fix, still running old code, would
    #    still hold this one. Drop this fallback after one release.
    pid_files = {VaultLayout(vault_dir).worker_pid, paths.WORKER_PID_FILE, paths.DATA_DIR / "worker.pid"}
    for pid_file in pid_files:
        if live_pid(pid_file) is not None:
            raise VaultSyncError(
                "the ingest worker is running for this vault -- wait for it to finish "
                "(`artmind ingest job-status`), then re-run `vault sync`"
            )


def _diff_name_status(vault_dir: Path, base: str, head: str, scope: Path) -> list[tuple[str, str]]:
    """`[(status, path), ...]` for `scope` between `base` and `head`, paths
    relative to `vault_dir`. `status` is git's own single-letter code
    (A/M/D); `--no-renames` guarantees a delete+add pair rather than a
    single "R100" line regardless of the user's global git config, which
    the per-path classification in `_classify_kg_diff`/
    `_classify_structured_text_diff` depends on.

    `-z` (spec 2026-09-26 §14 A5): the output is `status NUL path NUL ...`
    with every path verbatim. Without it git C-quotes a non-ASCII path
    (`"Caf\\303\\251/..."`), which no caller can match."""
    rel_scope = scope.relative_to(vault_dir)
    out = _git(vault_dir, ["diff", "--no-renames", "--name-status", "-z", base, head, "--", str(rel_scope)])
    fields = out.split("\0")
    return [(fields[i][0], fields[i + 1]) for i in range(0, len(fields) - 1, 2) if fields[i]]


def _show(vault_dir: Path, rev: str, relpath: str) -> str | None:
    """`git show rev:relpath`'s content, or None if that path doesn't exist
    at `rev` -- reading a removed KG-staging folder's document.json from
    before it was removed, the only way to recover its `doc_id`.

    Distinguishes "rev exists but relpath wasn't in it" (the legitimate,
    expected case -- returns None) from "rev itself doesn't resolve" (a
    stale/rewritten cursor, or any other git failure) -- both exit 128 from
    `git show`, so treating them the same would silently skip a real
    retraction whenever a stored commit sha ever points at history that no
    longer exists, instead of failing loudly the way this module's
    "refused before writing anything" philosophy demands.
    """
    rc, _, _ = run_command(["git", "cat-file", "-e", f"{rev}^{{commit}}"], cwd=vault_dir, expected_codes=(128,))
    if rc != 0:
        raise VaultSyncError(f"{rev!r} does not resolve to a commit in this vault's git history")
    rc, out, _ = run_command(["git", "show", f"{rev}:{relpath}"], cwd=vault_dir, expected_codes=(128,))
    return out if rc == 0 else None


def _materialize(vault_dir: Path, rev: str, relpaths: list[str], dest: Path) -> None:
    """Extract `relpaths` (files or folders, relative to the vault root) as
    they stand at `rev` into `dest`, keeping their vault-relative layout.

    This is what makes `sync` apply committed content only (spec 2026-09-26
    §6 A1): the working tree may hold uncommitted edits, a half-written
    staging folder, or a merge in progress, none of which is `rev`.
    `git archive` reads blobs straight from the object store, as bytes.
    """
    if not relpaths:
        return
    # Not `run_command`: it hardcodes `text=True`, which would decode/corrupt
    # the binary tar bytes `git archive` writes to stdout.
    proc = subprocess.run(
        ["git", "archive", "--format=tar", rev, "--", *relpaths],
        cwd=vault_dir, capture_output=True,
    )
    if proc.returncode != 0:
        raise VaultSyncError(
            f"git archive {rev} failed: {proc.stderr.decode(errors='replace').strip()}"
        )
    with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as tar:
        tar.extractall(dest, filter="data")


def _present_at(vault_dir: Path, rev: str, relpaths: list[str]) -> list[str]:
    """The subset of file paths `relpaths` that exist at `rev`, in order.
    `git archive` refuses a pathspec that matches nothing, and a table's
    `.meta.json` (or the legacy `manifest.json`) may legitimately be absent.
    NUL-separated (`-z`, §14 A5), so a non-ASCII name is not quoted away."""
    if not relpaths:
        return []
    present = set(_git(vault_dir, ["ls-tree", "-r", "-z", "--name-only", rev, "--", *relpaths]).split("\0"))
    return [p for p in relpaths if p in present]


def _files_at(vault_dir: Path, rev: str, scope: Path) -> list[str]:
    """Every file under `scope` at `rev`, vault-relative, from a NUL-separated
    listing (§14 A5). Raises `ValueError` when `scope` is outside
    `vault_dir`, like `_diff_name_status`."""
    rel_scope = scope.relative_to(vault_dir)
    out = _git(vault_dir, ["ls-tree", "-r", "-z", "--name-only", rev, "--", str(rel_scope)])
    return [p for p in out.split("\0") if p]


def _dir_at(vault_dir: Path, rev: str, live_dir: Path, scratch: Path) -> Path:
    """`live_dir` as committed at `rev`, materialised under `scratch` -- the
    table mappings and domain schemas track B projects with (phase 1 review
    finding 1, spec 2026-09-26 §14 A1). The returned path may not exist (no
    such files at `rev`); `find_mappings` then finds none and `load_schema`
    returns `{}`.

    A `live_dir` outside the vault (a run folder named by an explicit
    `ARTMIND_HOME`) is not versioned with it, so it is returned unchanged:
    there is no committed copy to prefer."""
    try:
        rel = live_dir.relative_to(vault_dir)
    except ValueError:
        return live_dir
    _materialize(vault_dir, rev, _files_at(vault_dir, rev, live_dir), scratch)
    return scratch / rel


def _vault_relative_mapping_error(e: Exception, scratch: Path, head: str) -> str:
    """A `MappingError` raised against the `_dir_at` scratch copy names that
    copy's absolute path -- gone by the time the user reads the message.
    Strip the scratch prefix back to the vault-relative path they actually
    have, and say which `head` it was read at."""
    return f"{str(e).replace(str(scratch) + '/', '')} (at {head[:12]})"


from dataclasses import dataclass, field


@dataclass
class SyncPlan:
    base: str
    head: str
    replay_docs: list[tuple[str, str]] = field(default_factory=list)        # (domain, docdir)
    retract: list[tuple[str, str]] = field(default_factory=list)            # (domain, doc_id)
    regenerate_tables: list[tuple[str, str]] = field(default_factory=list)  # (domain, table_name)
    same_as_keys: list[tuple[str, str, str]] = field(default_factory=list)  # track D: keys to rebuild
    same_as_groups: int = 0                                                 # track D: groups added/removed/changed
    curation: list[tuple[str, str, str]] = field(default_factory=list)      # track C: (kind, record_id, "apply"|"remove")


def _in_scope(domain: str, domains: list[str] | None) -> bool:
    if not domains:
        return True
    return any(domain == d or domain.startswith(d + ".") for d in domains)


def _document_ids_at_head(vault_dir: Path, head: str, kg_rel: Path, domains: list[str]) -> dict[str, set[str]]:
    """`{domain: {doc ids live at head}}` -- the id of every folder under
    `kg_rel/<domain>` at `head` that still has an `observations.json` there
    (the same test `replay` uses; a folder missing it is being retracted
    itself, not a live target another folder's id could hide behind).

    Scans ALL folders currently committed under the domain, not only the
    ones this run's diff touched, since the folder a moved/renamed note
    landed in may have been added (and already replayed) in an earlier sync
    (spec 2026-09-27 item 2's two-run case).

    Never feeds a PATH to `git cat-file` (re-review item 1): a folder that
    has `observations.json` but no `document.json` at `head` -- e.g. a
    half-written folder, or simply one that never got one -- would make
    `<head>:<path>` reply "missing", which a size-shaped parser can only
    mishandle. Instead: one `git ls-tree` (no `--name-only`, so it hands
    back each blob's oid) to find which folders have both files and which
    oid their `document.json` is, then one `git cat-file --batch` fed those
    oids directly -- oids ls-tree already proved exist, so "missing" cannot
    occur.
    """
    from artmind.atomic_dir import is_scratch

    if not domains:
        return {}
    rel_scopes = [str(kg_rel / d) for d in sorted(set(domains))]
    # No `--name-only`: each NUL-separated entry is `<mode> <type> <oid>\t<path>`.
    listing = _git(vault_dir, ["ls-tree", "-r", "-z", head, "--", *rel_scopes])
    folders_with_obs: set[tuple[str, str]] = set()
    doc_oid: dict[tuple[str, str], str] = {}
    for entry in listing.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        try:
            parts = Path(path).relative_to(kg_rel).parts
        except ValueError:
            continue
        if len(parts) != 3:
            continue
        domain, docdir, filename = parts
        if docdir.startswith("table__") or is_scratch(Path(docdir)):
            continue
        if filename == "observations.json":
            folders_with_obs.add((domain, docdir))
        elif filename == "document.json":
            fields = meta.split(" ")
            if len(fields) == 3 and fields[1] == "blob":
                doc_oid[(domain, docdir)] = fields[2]
    if not folders_with_obs:
        return {}
    # Only folders with BOTH files: one missing document.json (any reason --
    # half-written, deleted, never written) contributes no id, not a crash.
    targets = [(d, docdir, doc_oid[(d, docdir)]) for d, docdir in sorted(folders_with_obs) if (d, docdir) in doc_oid]
    if not targets:
        return {}

    blobs = _cat_blobs(vault_dir, [oid for _, _, oid in targets])
    ids_by_domain: dict[str, set[str]] = {}
    for domain, _docdir, oid in targets:
        doc_id = _document_id(blobs.get(oid))
        if doc_id is not None:
            ids_by_domain.setdefault(domain, set()).add(doc_id)
    return ids_by_domain


def _cat_blobs(vault_dir: Path, oids: list[str]) -> dict[str, bytes]:
    """`{oid: raw bytes}` for blob `oids`, in ONE `git cat-file --batch`.
    Raw object bytes: no smudge, eol or LFS filter is applied (unlike
    `git archive`/checkout). Callers pass oids `git ls-tree` just listed, so
    "missing" cannot occur; a non-blob answer is skipped."""
    wanted = list(dict.fromkeys(oids))
    if not wanted:
        return {}
    proc = subprocess.run(
        ["git", "cat-file", "--batch"], cwd=vault_dir,
        input="".join(f"{oid}\n" for oid in wanted).encode(), capture_output=True,
    )
    if proc.returncode != 0:
        raise VaultSyncError(f"git cat-file --batch failed: {proc.stderr.decode(errors='replace').strip()}")
    out = proc.stdout
    blobs: dict[str, bytes] = {}
    pos = 0
    for oid in wanted:
        nl = out.index(b"\n", pos)
        fields = out[pos:nl].split(b" ")
        pos = nl + 1
        if len(fields) != 3:
            continue
        _oid, typ, size_field = fields
        try:
            size = int(size_field)
        except ValueError:
            continue
        content, pos = out[pos:pos + size], pos + size + 1  # +1: cat-file's own trailing "\n"
        if typ == b"blob":
            blobs[oid] = content
    return blobs


def _document_id(content: bytes | None) -> str | None:
    """`id` from a document.json's bytes, or None when it has none -- not
    JSON, not UTF-8, or not an object (e.g. a bare list: not ours to read)."""
    import json

    if content is None:
        return None
    try:
        obj = json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return obj.get("id") if isinstance(obj, dict) else None


def committed_fingerprints(
    vault_dir: Path, head: str, folders: list[tuple[str, str]]
) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    """`{(domain, docdir): (doc_id, fingerprint)}` for KG staging folders as
    committed at `head` -- `sync_state.staging_fingerprint` over the committed
    blobs, the same bytes ingest hashed when it wrote them. One `git ls-tree`
    and one `git cat-file --batch` for every folder together."""
    import paths
    from artmind.sync_state import FINGERPRINTED_FILES, staging_fingerprint

    if not folders:
        return {}
    kg_rel = paths.KG_DIR.relative_to(vault_dir)
    scopes = [str(kg_rel / domain / docdir) for domain, docdir in folders]
    listing = _git(vault_dir, ["ls-tree", "-r", "-z", head, "--", *scopes])
    oid_of: dict[tuple[str, str, str], str] = {}
    for entry in listing.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = Path(path).relative_to(kg_rel).parts
        fields = meta.split(" ")
        if len(parts) == 3 and parts[2] in FINGERPRINTED_FILES and len(fields) == 3 and fields[1] == "blob":
            oid_of[parts] = fields[2]
    blobs = _cat_blobs(vault_dir, list(oid_of.values()))

    result = {}
    for domain, docdir in folders:
        def _read(name, _d=domain, _f=docdir):
            oid = oid_of.get((_d, _f, name))
            return blobs.get(oid) if oid else None

        result[(domain, docdir)] = (_document_id(_read("document.json")), staging_fingerprint(_read))
    return result


def drop_unchanged(
    vault_dir: Path, plan: "SyncPlan", *, timeout: float | None = None
) -> list[tuple[str, str]]:
    """Spec 2026-09-26 §6 A3: remove from `plan.replay_docs` every folder
    whose committed fingerprint the graph's `Document` already carries, and
    return them. ONE Cypher read for the whole plan. On a shared graph the
    machine that ingested a document already wrote it, so this is what makes
    the other machine's apply a near no-op; on a separate local Neo4j nothing
    matches and everything is replayed."""
    from artmind import sync_state

    committed = committed_fingerprints(vault_dir, plan.head, plan.replay_docs)
    doc_ids = [doc_id for doc_id, fp in committed.values() if doc_id is not None and fp is not None]
    if not doc_ids:
        return []
    in_graph = sync_state.read_document_fingerprints(doc_ids, timeout=timeout)
    unchanged = [key for key in plan.replay_docs if _is_unchanged(committed.get(key), in_graph)]
    plan.replay_docs = [key for key in plan.replay_docs if key not in unchanged]
    return unchanged


def _is_unchanged(committed: tuple[str | None, str | None] | None, in_graph: dict) -> bool:
    """Does the graph's live Document already carry this committed folder's
    fingerprint?"""
    if committed is None:
        return False
    doc_id, fingerprint = committed
    return fingerprint is not None and doc_id in in_graph and in_graph[doc_id] == fingerprint


def _classify_kg_diff(
    vault_dir: Path, base: str, head: str, domains: list[str] | None
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Track A (spec §4.A): replay/retract for every document folder under
    `.artmind/data/kg/**`.

    Grouped by folder (spec 2026-09-26 §14 A4): a folder in which ANY file
    changed between `base` and `head` is replayed once, if its
    `observations.json` exists at `head` -- a timer commit can catch a folder
    in two halves, and the commit carrying the second half must replay it
    again. A folder whose `observations.json` was removed is retracted, by the
    doc id its `document.json` had at `base`. A folder with changes but no
    `observations.json` at either end (a half-written folder from a
    non-atomic writer) has nothing to replay yet.

    Skipped entirely: `table__<name>` folders -- spec 2026-09-26 §5 R4:
    gitignored, a pure function of a table's CSV/mapping, regenerated by
    track B (without this, the `git rm -r --cached` that `vault doctor`
    recommends would retract that table on every receiving machine) -- and
    `.artmind-tmp`/`.artmind-old` atomic-write scratch.

    Reads committed history only (`git diff`/`ls-tree`/`show` between two
    fixed revisions), never the live filesystem, which track B may already
    have written fresh, uncommitted content into by the time this runs."""
    import json

    import paths
    from artmind.atomic_dir import is_scratch

    kg_dir = paths.KG_DIR
    try:
        rows = _diff_name_status(vault_dir, base, head, kg_dir)
    except ValueError:
        # Only unreachable in production, where KG_DIR/STRUCTURED_TEXT_DIR
        # are always under the same vault root -- some tests intentionally
        # patch only one of the two (test/conftest.py's autouse fixture
        # patches STRUCTURED_TEXT_DIR for every test but has no equivalent
        # for KG_DIR), leaving this one pointed outside `vault_dir`.
        return [], []
    kg_rel = kg_dir.relative_to(vault_dir)

    # (domain, docdir) -> [(status, path inside the folder)], in git's order.
    folders: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for status, path in rows:
        parts = Path(path).relative_to(kg_rel).parts
        if len(parts) < 3:
            continue
        domain, docdir = parts[0], parts[1]
        if docdir.startswith("table__") or is_scratch(Path(docdir)):
            continue
        if not _in_scope(domain, domains):
            continue
        folders.setdefault((domain, docdir), []).append((status, "/".join(parts[2:])))
    if not folders:
        return [], []

    obs_path = {key: str(kg_rel / key[0] / key[1] / "observations.json") for key in folders}
    at_head = set(_present_at(vault_dir, head, list(obs_path.values())))

    replay: list[tuple[str, str]] = []
    retract_candidates: list[tuple[str, str]] = []  # (domain, doc_id)
    for (domain, docdir), changes in folders.items():
        if obs_path[(domain, docdir)] in at_head:
            replay.append((domain, docdir))
        elif ("D", "observations.json") in changes:
            # The doc's id lives in the sibling `document.json`, not in
            # `observations.json` itself (which is a bare list) -- read that
            # file's content as it stood at `base`, right before removal.
            base_content = _show(vault_dir, base, str(kg_rel / domain / docdir / "document.json"))
            if base_content is None:
                raise VaultSyncError(
                    f"{obs_path[(domain, docdir)]} is marked removed since {base}, but "
                    f"wasn't present at {base} either -- diff and history disagree, refusing to guess"
                )
            retract_candidates.append((domain, json.loads(base_content)["id"]))
    if not retract_candidates:
        return replay, []

    # A moved/renamed note keeps its `_artmind_id`; its staging folder just
    # changes name, so the old folder's removal above looks like a delete of
    # that id. Don't retract an id any `document.json` at `head`, in the
    # same domain, still carries -- whether the new folder appeared in THIS
    # diff range (replayed above) or in an earlier one already applied
    # (spec 2026-09-27 item 2).
    live = _document_ids_at_head(vault_dir, head, kg_rel, [d for d, _ in retract_candidates])
    retract = [(d, doc_id) for d, doc_id in retract_candidates if doc_id not in live.get(d, set())]
    return replay, retract


def _classify_structured_text_diff(
    vault_dir: Path, base: str, head: str, domains: list[str] | None
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Track B (spec §4.B): regenerate/retract for every table's CSV or
    `.meta.json` under `.artmind/data/structured_text/**`. A legacy
    `manifest.json` change on its own still triggers nothing (a documented
    scoping decision); per-table meta (spec 2026-09-26 R5) is what makes a
    metadata-only change visible."""
    import paths

    st_dir = paths.STRUCTURED_TEXT_DIR
    try:
        rows = _diff_name_status(vault_dir, base, head, st_dir)
    except ValueError:
        # Only unreachable in production, where KG_DIR/STRUCTURED_TEXT_DIR
        # are always under the same vault root -- some tests intentionally
        # patch only one of the two, leaving this one pointed outside
        # `vault_dir`. Symmetric with `_classify_kg_diff`'s guard above.
        return [], []
    st_rel = st_dir.relative_to(vault_dir)

    regenerate: list[tuple[str, str]] = []
    retract: list[tuple[str, str]] = []
    for status, path in rows:
        rel = Path(path).relative_to(st_rel)
        parts = rel.parts
        if len(parts) != 2:
            continue
        domain, filename = parts
        if filename.endswith(".csv"):
            table_name, is_csv = filename[: -len(".csv")], True
        elif filename.endswith(_META_SUFFIX):
            table_name, is_csv = filename[: -len(_META_SUFFIX)], False
        else:
            continue
        if not _in_scope(domain, domains):
            continue
        key = (domain, table_name)
        if status in ("A", "M") and key not in regenerate:
            regenerate.append(key)
        elif status == "D" and is_csv:
            retract.append((domain, f"table:{domain}:{table_name}"))
    # A table whose CSV is gone is retracted; regenerating it would fail.
    retracted = {(d, doc_id.split(":", 2)[2]) for d, doc_id in retract}
    regenerate = [key for key in regenerate if key not in retracted]
    return regenerate, retract


def _mapping_at(vault_dir: Path, rev: str, relpath: str):
    """The table mapping at `relpath` as committed at `rev` -- never the
    working tree (spec 2026-09-26 §14 A1). Raises `MappingError` (a
    `ValueError`) for a mapping that does not parse."""
    import yaml

    from artmind.table2graph import MappingError, parse_mapping

    text = _show(vault_dir, rev, relpath)
    if text is None:
        raise VaultSyncError(f"{relpath} is not in {rev}")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise MappingError(f"{relpath}: invalid YAML: {e}") from None
    return parse_mapping(data, Path(relpath))


def _mapping_at_head_or_raise(vault_dir: Path, head: str, relpath: str):
    """`_mapping_at` at `head`, wrapping a parse failure as `VaultSyncError`
    with the "(at <head>)" suffix every caller that must fail loudly on an
    unparseable mapping at `head` uses -- so the message reads the same
    whether the diff classifier or `_mappings_at_head`'s own full listing is the
    one that found it broken."""
    from artmind.table2graph import MappingError

    try:
        return _mapping_at(vault_dir, head, relpath)
    except MappingError as e:
        raise VaultSyncError(f"{e} (at {head[:12]})") from None


def _mappings_at_head(vault_dir: Path, head: str) -> list:
    """Every table mapping `find_mappings` would see, as committed at `head`.
    Head only: an unparseable mapping raises (see `_mapping_at_head_or_raise`),
    which is right for `head` and wrong for `base`, where the spec skips it.
    A mappings dir outside the vault is not versioned with it (see
    `_dir_at`); its live copy is all there is."""
    import paths
    from artmind.table2graph import load_mapping

    live = paths.TABLE_MAPPINGS_DIR
    try:
        rel = live.relative_to(vault_dir)
    except ValueError:
        return [load_mapping(p) for p in sorted(live.glob("*.yaml"))] if live.is_dir() else []
    return [
        _mapping_at_head_or_raise(vault_dir, head, p)
        for p in _files_at(vault_dir, head, live)
        if Path(p).parent == rel and p.endswith(".yaml")
    ]


def _tables_at(vault_dir: Path, rev: str) -> list[tuple[str, str]]:
    """`(domain, table_name)` for every structured-text CSV committed at
    `rev`: the tables this vault can restore, and so the tables a mapping or
    schema change can re-project."""
    import paths

    st_dir = paths.STRUCTURED_TEXT_DIR
    try:
        files = _files_at(vault_dir, rev, st_dir)
    except ValueError:
        return []
    st_rel = st_dir.relative_to(vault_dir)
    tables = []
    for path in files:
        parts = Path(path).relative_to(st_rel).parts
        if len(parts) == 2 and parts[1].endswith(".csv"):
            tables.append((parts[0], parts[1][: -len(".csv")]))
    return tables


def _classify_table_sources(
    vault_dir: Path, base: str, head: str, domains: list[str] | None
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Spec 2026-09-26 §14 A1: `table__*` folders are gitignored (R4), so a
    table whose CSV did not change but whose mapping or domain schema did
    would otherwise keep its old projection on every other machine.

    - A mapping (`TABLE_MAPPINGS_DIR/*.yaml`) added, changed or removed
      between `base` and `head` affects every table at `head` its `table:`
      pattern matched at either end -- so a narrowed pattern reaches the
      tables it stopped covering.
    - A domain schema (`DOMAIN_SCHEMAS_DIR/<d>_schema.yaml`) added, changed
      or removed affects the tables of `d` and of its dotted children (a
      child inherits the parent's `temporal:` block, `temporal.load_schema`).

    An affected table some mapping at `head` matches is regenerated. One that
    only a mapping change affected, and that no mapping at `head` matches, is
    retracted (`table:<domain>:<table>`). A schema-affected table no mapping
    matches is a structured-store table only (§14 A2): nothing to do.

    Every mapping is read from git, never the working tree. One that does not
    parse at `head` fails the sync; one that does not parse at `base` is
    skipped with a warning -- refusing would stall sync on history no edit
    can fix."""
    import paths
    from artmind.table2graph import MappingError

    def _rows(live_dir: Path, suffix: str) -> list[tuple[str, str]]:
        try:
            rel = live_dir.relative_to(vault_dir)
        except ValueError:
            return []  # not versioned with the vault: nothing in the diff
        rows = _diff_name_status(vault_dir, base, head, live_dir)
        return [(s, p) for s, p in rows if Path(p).parent == rel and p.endswith(suffix)]

    mapping_rows = _rows(paths.TABLE_MAPPINGS_DIR, ".yaml")
    schema_rows = _rows(paths.DOMAIN_SCHEMAS_DIR, "_schema.yaml")
    if not mapping_rows and not schema_rows:
        return [], []

    tables = _tables_at(vault_dir, head)

    by_mapping: set[tuple[str, str]] = set()
    for status, path in mapping_rows:
        versions = []
        if status in ("A", "M"):
            versions.append(_mapping_at_head_or_raise(vault_dir, head, path))
        if status in ("M", "D"):
            try:
                versions.append(_mapping_at(vault_dir, base, path))
            except MappingError as e:
                logger.warning(
                    "vault sync: {} at {} does not parse, so tables only it matched are not retracted: {}",
                    path, base[:12], e,
                )
        for mapping in versions:
            by_mapping.update(key for key in tables if mapping.matches(key[1], key[0]))

    schema_domains = {Path(p).name[: -len("_schema.yaml")] for _, p in schema_rows}
    by_schema = {
        key for key in tables if any(key[0] == d or key[0].startswith(d + ".") for d in schema_domains)
    }

    head_mappings = _mappings_at_head(vault_dir, head)
    regenerate: list[tuple[str, str]] = []
    retract: list[tuple[str, str]] = []
    for domain, table_name in sorted(by_mapping | by_schema):
        if not _in_scope(domain, domains):
            continue
        if any(m.matches(table_name, domain) for m in head_mappings):
            regenerate.append((domain, table_name))
        elif (domain, table_name) in by_mapping:
            retract.append((domain, f"table:{domain}:{table_name}"))
    return regenerate, retract


# ── track D: same_as.yaml (spec 2026-09-26 §7) ────────────────────────────────


def _same_as_relpath(vault_dir: Path) -> str | None:
    """`same_as.yaml`'s path relative to the vault, or None when it lives
    outside it (a run folder named by an explicit `ARTMIND_HOME`): then it is
    not versioned with the vault, and there is nothing to diff."""
    from artmind import same_as

    try:
        return str(Path(same_as.SAME_AS_PATH).relative_to(vault_dir))
    except ValueError:
        return None


def same_as_groups_at(vault_dir: Path, rev: str) -> list[list[tuple[str, str, str]]]:
    """The same-as groups as committed at `rev` -- never the working tree,
    where an uncommitted edit may sit (spec 2026-09-26 §6 A1). The empty tree
    (`--bootstrapEmpty`'s base) has none. A `same_as.yaml` outside the vault
    is not versioned with it, so its live copy is all there is."""
    from artmind import same_as

    rel = _same_as_relpath(vault_dir)
    if rel is None:
        return same_as.load_groups()
    if rev == EMPTY_TREE_SHA:
        return []
    return same_as.parse_groups(_show(vault_dir, rev, rel), source=f"{rel} at {rev[:12]}")


def _group_identity(group: list[tuple[str, str, str]]) -> tuple:
    """A group as a comparable value: its canonical and its member set. Member
    order in the file carries no meaning; which member is canonical does."""
    return (group[0], tuple(sorted(group)))


def _classify_same_as(
    vault_dir: Path, base: str, head: str, domains: list[str] | None
) -> tuple[list[tuple[str, str, str]], int]:
    """Track D: `(keys, groups)` -- every aggregate key in a same-as group
    added, removed or changed between `base` and `head`, and how many such
    groups there were. A removed group's members must be rebuilt as separate
    entities again; an added group's, folded (or linked); a changed group's,
    both, so the keys of its `base` AND `head` versions are included.

    Scoped like the other tracks: a group counts when any member's domain is
    in scope (then all its members are rebuilt -- a group is rebuilt whole).
    Reads `same_as.yaml` at both revisions from git; an unchanged file costs
    one `git diff` and nothing else."""
    rel = _same_as_relpath(vault_dir)
    if rel is None:
        return [], 0
    if not _diff_name_status(vault_dir, base, head, vault_dir / rel):
        return [], 0
    before = {_group_identity(g) for g in same_as_groups_at(vault_dir, base)}
    after = {_group_identity(g) for g in same_as_groups_at(vault_dir, head)}
    changed = [
        members for _, members in sorted(before ^ after)
        if any(_in_scope(key[2], domains) for key in members)
    ]
    keys = sorted({key for members in changed for key in members})
    return keys, len(changed)


# ── track C: curation records (spec 2026-09-26 §7, §14 A6) ───────────────────


def _curation_rel(vault_dir: Path) -> Path | None:
    """`paths.CURATION_DIR` relative to the vault, or None outside it."""
    import paths

    try:
        return Path(paths.CURATION_DIR).relative_to(vault_dir)
    except ValueError:
        return None


def _curation_relpath(vault_dir: Path, kind: str, record_id: str) -> str:
    return str(_curation_rel(vault_dir) / kind / f"{record_id}.json")


def _blobs_at(vault_dir: Path, rev: str, relpaths: list[str]) -> dict[str, bytes]:
    """`{relpath: committed bytes}` for the files among `relpaths` that exist
    at `rev`: one `git ls-tree` and one `git cat-file --batch`, raw blobs
    (the bytes `curation_records.fingerprint` hashes)."""
    if not relpaths:
        return {}
    listing = _git(vault_dir, ["ls-tree", "-r", "-z", rev, "--", *relpaths])
    oid_of: dict[str, str] = {}
    for entry in listing.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        fields = meta.split(" ")
        if len(fields) == 3 and fields[1] == "blob":
            oid_of[path] = fields[2]
    blobs = _cat_blobs(vault_dir, list(oid_of.values()))
    return {path: blobs[oid] for path, oid in oid_of.items() if oid in blobs}


def _classify_curation(
    vault_dir: Path, base: str, head: str, domains: list[str] | None
) -> list[tuple[str, str, str]]:
    """Track C: `(kind, record_id, action)` for every curation record file
    (`curation/<kind>/<id>.json`, a registered kind) added or changed
    (`"apply"`) or deleted (`"remove"`) between `base` and `head`.

    `--domain` scoping reads the record -- as committed at `head` for an
    apply, at `base` for a removal -- and keeps it when any of its domains is
    in scope."""
    from artmind import curation_records

    rel = _curation_rel(vault_dir)
    if rel is None:
        return []
    known = curation_records.kinds()
    changes: list[tuple[str, str, str]] = []
    for status, path in _diff_name_status(vault_dir, base, head, vault_dir / rel):
        parts = Path(path).relative_to(rel).parts
        if len(parts) != 2 or not parts[1].endswith(".json") or parts[0] not in known:
            continue
        changes.append((parts[0], parts[1][: -len(".json")], "remove" if status == "D" else "apply"))
    if not domains or not changes:
        return changes
    at_head = _blobs_at(vault_dir, head, [_curation_relpath(vault_dir, k, i) for k, i, a in changes if a == "apply"])
    at_base = _blobs_at(vault_dir, base, [_curation_relpath(vault_dir, k, i) for k, i, a in changes if a == "remove"])
    scoped = []
    for kind, record_id, action in changes:
        blobs = at_head if action == "apply" else at_base
        record = curation_records.parse(blobs.get(_curation_relpath(vault_dir, kind, record_id)))
        if record is not None and any(_in_scope(d, domains) for d in known[kind].domains(record)):
            scoped.append((kind, record_id, action))
    return scoped


def committed_curation_fingerprints(vault_dir: Path, head: str, plan: "SyncPlan") -> dict[tuple[str, str], str]:
    """`{(kind, record_id): fingerprint}` of every record `plan` applies, as
    committed at `head`."""
    from artmind import curation_records

    wanted = {(k, i): _curation_relpath(vault_dir, k, i) for k, i, a in plan.curation if a == "apply"}
    blobs = _blobs_at(vault_dir, head, list(wanted.values()))
    return {key: curation_records.fingerprint(blobs[path]) for key, path in wanted.items() if path in blobs}


def _graph_curation_fingerprints(
    items: list[tuple[str, str, str]], *, timeout: float | None
) -> dict[tuple[str, str], str | None]:
    """`{(kind, record_id): fingerprint}` the graph holds, one query per kind
    that has items. A record the graph does not hold is absent."""
    from artmind import curation_records

    by_kind: dict[str, list[str]] = {}
    for kind, record_id, _ in items:
        by_kind.setdefault(kind, []).append(record_id)
    known = curation_records.kinds()
    found: dict[tuple[str, str], str | None] = {}
    for kind, ids in by_kind.items():
        for record_id, fp in known[kind].read_fingerprints(ids, timeout=timeout).items():
            found[(kind, record_id)] = fp
    return found


def drop_unchanged_curation(vault_dir: Path, plan: "SyncPlan", *, timeout: float | None = None) -> int:
    """Spec §6 A3 for curation: remove from `plan.curation` every record
    whose committed fingerprint the graph already carries (the machine that
    wrote it, sharing this graph, already applied it) and return how many.
    Removals are always kept (removing an absent record is a no-op)."""
    committed = committed_curation_fingerprints(vault_dir, plan.head, plan)
    applies = [item for item in plan.curation if item[2] == "apply"]
    if not applies:
        return 0
    in_graph = _graph_curation_fingerprints(applies, timeout=timeout)
    unchanged = [
        (k, i, a) for k, i, a in applies
        if committed.get((k, i)) is not None and in_graph.get((k, i)) == committed[(k, i)]
    ]
    plan.curation = [item for item in plan.curation if item not in unchanged]
    return len(unchanged)


def reapply_for_replayed(vault_dir: Path, plan: "SyncPlan") -> int:
    """Add to `plan.curation` the records a kind asks to re-apply after the
    documents `plan` replays (`Kind.reapply_for`) -- a replay revives a
    document's node, and a retirement committed at `head` must still win.
    Only records that exist at `head`; returns how many were added."""
    from artmind import curation_records

    known = curation_records.kinds()
    hooks = {name: kind for name, kind in known.items() if kind.reapply_for}
    if not hooks or not plan.replay_docs or _curation_rel(vault_dir) is None:
        return 0
    doc_ids = [doc_id for doc_id, _ in committed_fingerprints(vault_dir, plan.head, plan.replay_docs).values() if doc_id]
    wanted = [(name, rid) for name, kind in hooks.items() for rid in kind.reapply_for(doc_ids)]
    present = _blobs_at(vault_dir, plan.head, [_curation_relpath(vault_dir, n, r) for n, r in wanted])
    added = 0
    for name, rid in wanted:
        item = (name, rid, "apply")
        if _curation_relpath(vault_dir, name, rid) in present and item not in plan.curation:
            plan.curation.append(item)
            added += 1
    return added


def _curation_counts(items: list[tuple[str, str, str]]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for kind, _, action in items:
        counts.setdefault(kind, {"apply": 0, "remove": 0})[action] += 1
    return counts


def _apply_curation(
    vault_dir: Path, base: str, head: str, items: list[tuple[str, str, str]], phase: str
) -> set:
    """Apply (or remove) every item of `phase` -- an applied record from its
    committed bytes at `head`, a removed one from its bytes at `base` (the
    last version that existed), one write transaction per record -- and
    return the aggregate keys the kinds report changed."""
    from artmind import curation_records, graph_query

    known = curation_records.kinds()
    items = [item for item in items if known[item[0]].phase == phase]
    if not items:
        return set()
    blobs = _blobs_at(vault_dir, head, [_curation_relpath(vault_dir, k, i) for k, i, a in items if a == "apply"])
    removed = _blobs_at(vault_dir, base, [_curation_relpath(vault_dir, k, i) for k, i, a in items if a == "remove"])
    keys: set = set()
    with graph_query.neo4j_session() as session:
        for kind_name, record_id, action in items:
            kind = known[kind_name]
            if action == "remove":
                record = curation_records.parse(removed.get(_curation_relpath(vault_dir, kind_name, record_id)))
                if record is None or record.get("id") != record_id:
                    logger.warning(
                        "vault sync: the {} record {} is unreadable at its base commit {}, so it is removed "
                        "from a stub {{'id': ...}} record -- a kind that needs the record body cannot use it",
                        kind_name, record_id, base[:12],
                    )
                    record = {"id": record_id}
                keys |= set(session.execute_write(kind.remove, record) or ())
                continue
            relpath = _curation_relpath(vault_dir, kind_name, record_id)
            data = blobs.get(relpath)
            record = curation_records.parse(data)
            if record is None or record.get("id") != record_id:
                raise VaultSyncError(f"{relpath} at {head[:12]} is not a {kind_name} record for id {record_id!r}")
            keys |= set(session.execute_write(kind.apply, record, curation_records.fingerprint(data)) or ())
    return {tuple(k) for k in keys}


def classify_diff(
    vault_dir: Path, base: str, head: str, domains: list[str] | None = None
) -> SyncPlan:
    """The full classified diff for one sync run (spec §4), over the FIXED
    `base..head` range the caller has already resolved -- never re-queried
    mid-run, which is what makes §5's sequencing guarantee hold.

    `regenerate_tables` and `retract` may both name the same table: when its
    CSV changed and its mapping was removed in the same range, it is
    re-restored to DuckDB (track B) and also retracted from the graph
    (`table:<domain>:<table>` no mapping covers any more) in the same run."""
    plan = SyncPlan(base=base, head=head)
    plan.replay_docs, kg_retract = _classify_kg_diff(vault_dir, base, head, domains)
    plan.regenerate_tables, table_retract = _classify_structured_text_diff(vault_dir, base, head, domains)
    source_regenerate, source_retract = _classify_table_sources(vault_dir, base, head, domains)
    for key in source_regenerate:
        if key not in plan.regenerate_tables:
            plan.regenerate_tables.append(key)

    seen: set[tuple[str, str]] = set()
    for domain, doc_id in kg_retract + table_retract + source_retract:
        if (domain, doc_id) not in seen:
            seen.add((domain, doc_id))
            plan.retract.append((domain, doc_id))
    plan.same_as_keys, plan.same_as_groups = _classify_same_as(vault_dir, base, head, domains)
    plan.curation = _classify_curation(vault_dir, base, head, domains)
    return plan


# ── bookmarks (spec 2026-09-26 §6 A2) ────────────────────────────────────────

#: The stores `vault sync` applies to. Each has its own bookmark ("this store
#: reflects commits up to X"), because a shared AuraDB and a local DuckDB can
#: be at different commits.
STORES = ("graph", "structured")

#: `state.json` key of the structured store's bookmark. DuckDB is always a
#: local file, so its bookmark is machine-local too. The graph's lives in the
#: graph (`artmind.sync_state`).
STRUCTURED_BOOKMARK_KEY = "last_structured_commit"

#: The single pre-bookmark cursor. Read as the starting point of any store
#: that has no bookmark of its own yet; removed from `state.json` by the first
#: run after which both stores have one.
LEGACY_CURSOR_KEY = "last_synced_commit"


@dataclass
class Bookmarks:
    """Each store's own bookmark as read, plus the legacy cursor."""

    graph: str | None = None
    structured: str | None = None
    legacy: str | None = None

    def own(self, store: str) -> str | None:
        return self.graph if store == "graph" else self.structured

    def effective(self, store: str) -> str | None:
        """Where `store` starts from: its own bookmark, else the legacy cursor.

        The graph's own bookmark always wins over a legacy cursor, even when
        they differ -- on a shared graph the other machine set it, and it
        describes the store itself, whereas the legacy cursor only recorded
        what THIS machine last applied. Starting the graph from a bookmark
        that is too old only re-applies idempotent work (fingerprints make it
        cheap); starting it from one that is too new would skip work. The
        legacy cursor remains exactly right for this machine's DuckDB."""
        own = self.own(store)
        return own if own is not None else self.legacy


def vault_id_or_raise(vault_dir: Path) -> str:
    """This vault's `vault_id` (`.artmind/vault.yaml`), which keys the graph
    bookmark. Raises when the vault predates it."""
    from artmind.manifest import read_vault_id

    vault_id = read_vault_id(vault_dir)
    if vault_id is None:
        raise VaultSyncError(
            "this vault has no vault_id in .artmind/vault.yaml yet -- run `artmind init` "
            "once (it appends one and changes nothing else), let Obsidian Git commit it, "
            "then re-run `vault sync`"
        )
    return vault_id


def read_bookmarks(
    vault_dir: Path, *, vault_id: str | None, stores: tuple[str, ...] = STORES, timeout: float | None = None
) -> Bookmarks:
    """Both stores' bookmarks and the legacy cursor. The graph is read only
    when `"graph"` is in `stores` -- a structured-only run never needs Neo4j."""
    from artmind import sync_state
    from artmind.vault import VaultLayout, read_state

    state = read_state(VaultLayout(vault_dir))
    graph = sync_state.read_graph_bookmark(vault_id, timeout=timeout) if "graph" in stores else None
    return Bookmarks(
        graph=graph,
        structured=state.get(STRUCTURED_BOOKMARK_KEY),
        legacy=state.get(LEGACY_CURSOR_KEY),
    )


def check_bookmark(vault_dir: Path, store: str, commit: str, head: str) -> None:
    """Refuse a bookmark that is not an ancestor of `head`.

    Diffing from a commit that is not in HEAD's history would read everything
    the bookmark has and HEAD lacks as removals: on a shared graph, where the
    other machine set the bookmark past this clone, that retracts the other
    machine's documents. Pulling first (Obsidian Git merges) makes the
    bookmark an ancestor again -- but a history rewrite (a rebase, or a
    branch reset) can make it unreachable for good, with nothing left to
    pull, so the refusal also names the bootstrap flags that get this store
    moving again without ever diffing from the stale bookmark."""
    rc, _, _ = run_command(
        ["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=vault_dir, expected_codes=(1, 128)
    )
    if rc != 0:
        if store == "graph":
            # Usually unpulled work from another machine on a shared graph --
            # but a commit rewritten away (a force-push) before this clone
            # ever fetched it is absent here for good, and no pull fixes that.
            raise VaultSyncError(
                f"the graph bookmark {commit[:12]} is not a commit in this clone -- another "
                "machine sharing this graph has applied commits this clone has not pulled "
                "yet. Let Obsidian Git pull, then re-run `vault sync`. If it is still not "
                "found after pulling (history was rewritten), re-run with `--store graph "
                "--bootstrapSynced` if graph is already known-current, or `--store graph "
                "--bootstrapEmpty` to replay everything committed."
            )
        raise VaultSyncError(
            f"the structured bookmark {commit[:12]} is not a commit in this clone (history "
            "was rewritten) -- re-run with `--store structured --bootstrapEmpty`"
        )
    rc, _, _ = run_command(
        ["git", "merge-base", "--is-ancestor", commit, head], cwd=vault_dir, expected_codes=(1,)
    )
    if rc != 0:
        raise VaultSyncError(
            f"the {store} bookmark {commit[:12]} is not an ancestor of HEAD {head[:12]} -- "
            "this clone is behind (or has diverged from) what that store already reflects. "
            "Let Obsidian Git pull and merge, then re-run `vault sync`. If that doesn't fix "
            "it (the bookmark's commit was rewritten away, not just unpulled), re-run with "
            f"`--store {store} --bootstrapSynced` if {store} is already known-current, or "
            f"`--store {store} --bootstrapEmpty` to replay everything committed."
        )


def _no_bookmark_message(missing: list[str], stores: tuple[str, ...]) -> str:
    message = (
        f"no {' or '.join(missing)} bookmark recorded yet for this vault -- pass "
        "--bootstrapEmpty to replay everything committed (correct for an empty "
        "Neo4j/DuckDB), or --bootstrapSynced if it is already known-current (e.g. "
        "right after `session initiate`/`db restore`)"
    )
    if len(missing) == 1 and len(stores) > 1:
        message += f"; add `--store {missing[0]}` to bootstrap that store alone"
    return message


def _advance_bookmarks(
    vault_dir: Path, vault_id: str | None, stores: tuple[str, ...], commit: str, marks: Bookmarks
) -> Bookmarks:
    """Move every store in `stores` to `commit`: the graph's node first, then
    `state.json`. Retires the legacy cursor once both stores genuinely have
    their own bookmark."""
    from artmind import sync_state
    from artmind.vault import VaultLayout, write_state

    new = Bookmarks(graph=marks.graph, structured=marks.structured, legacy=marks.legacy)
    # A store already at `commit` is not written again: a sync with nothing
    # new would only churn the graph node's `applied_at` (and state.json).
    if "graph" in stores:
        if marks.graph != commit:
            sync_state.write_graph_bookmark(vault_id, commit)
        new.graph = commit
    updates: dict = {}
    if "structured" in stores:
        if marks.structured != commit:
            updates[STRUCTURED_BOOKMARK_KEY] = commit
        new.structured = commit
    remove: tuple[str, ...] = ()
    if new.legacy is not None:
        # `marks` (and so `new`) only carries the graph bookmark when this
        # run's `stores` included "graph" -- `read_bookmarks` skips Neo4j
        # otherwise. So `new.graph is None` here means either "no graph
        # bookmark exists" or "this run never checked", and those are not the
        # same thing: a structured-only run following an earlier
        # `--store graph` run must not read the second as "graph has no
        # bookmark" and keep the legacy cursor forever. Re-read the graph
        # bookmark directly, but only in this narrow spot -- deciding
        # retirement when there's a legacy cursor to retire and this run
        # didn't already read the graph -- not on every sync call.
        graph_bookmark = new.graph
        if "graph" not in stores:
            from artmind.manifest import read_vault_id

            resolved_vault_id = vault_id if vault_id is not None else read_vault_id(vault_dir)
            graph_bookmark = (
                sync_state.read_graph_bookmark(resolved_vault_id) if resolved_vault_id is not None else None
            )
        if graph_bookmark is not None and new.structured is not None:
            remove = (LEGACY_CURSOR_KEY,)
            new.legacy = None
    if updates or remove:
        write_state(VaultLayout(vault_dir), updates, remove=remove)
    return new


def _bookmark_fields(marks: Bookmarks, stores: tuple[str, ...]) -> dict:
    return {f"{store}_bookmark": marks.effective(store) for store in stores}


def sync(
    vault_dir: Path,
    *,
    domains: list[str] | None = None,
    bootstrap_empty: bool = False,
    bootstrap_synced: bool = False,
    dry_run: bool = False,
    store: str | None = None,
) -> dict:
    """Replay committed KG-staging and structured-text changes into
    Neo4j/DuckDB since each store's bookmark (spec §5, spec 2026-09-26 §6 A2).

    `store` limits the run -- apply and bookmark -- to `"graph"` or
    `"structured"`; None runs both. Each store applies its own range
    (`bookmark..HEAD`); a graph ahead of the structured store (or the reverse)
    is normal. The bootstrap flags apply to every store in the run.

    All-or-nothing: bookmarks only advance on full success (§9); any
    exception propagates unchanged, leaving them untouched, so the next
    invocation recomputes the identical ranges. A `--domain`-scoped run never
    advances a bookmark."""
    if bootstrap_empty and bootstrap_synced:
        raise VaultSyncError("pass at most one of --bootstrapEmpty / --bootstrapSynced")
    if bootstrap_synced and domains:
        raise VaultSyncError("--bootstrapSynced stamps the whole vault; it can't be domain-scoped")
    if store is not None and store not in STORES:
        raise VaultSyncError(f"unknown store {store!r} -- one of: {', '.join(STORES)}")
    stores = (store,) if store else STORES

    head = head_sha(vault_dir)
    preflight(vault_dir)
    vault_id = vault_id_or_raise(vault_dir) if "graph" in stores else None
    marks = read_bookmarks(vault_dir, vault_id=vault_id, stores=stores)

    if bootstrap_synced:
        if dry_run:
            return {"bootstrap": "synced", "dry_run": True, "would_stamp": head, "stores": list(stores)}
        new = _advance_bookmarks(vault_dir, vault_id, stores, head, marks)
        return {"bootstrap": "synced", "stores": list(stores), **_bookmark_fields(new, stores)}

    bases: dict[str, str] = {}
    missing: list[str] = []
    for name in stores:
        base = EMPTY_TREE_SHA if bootstrap_empty else marks.effective(name)
        if base is None:
            missing.append(name)
        else:
            bases[name] = base
    if missing:
        raise VaultSyncError(_no_bookmark_message(missing, stores))
    for name, base in bases.items():
        if base != EMPTY_TREE_SHA:
            check_bookmark(vault_dir, name, base, head)

    # One classification per distinct base: the graph needs the full plan;
    # the structured store needs only its tables. When both stores start from
    # the same commit (the common case) the graph plan's tables ARE the
    # structured store's, so git is read once.
    if "graph" in stores:
        plan = classify_diff(vault_dir, bases["graph"], head, domains)
        unchanged = drop_unchanged(vault_dir, plan)
        curation_unchanged = drop_unchanged_curation(vault_dir, plan)
        reapply_for_replayed(vault_dir, plan)
    else:
        plan = SyncPlan(base=bases["structured"], head=head)
        unchanged = []
        curation_unchanged = 0
    project_tables = list(plan.regenerate_tables)
    if "structured" not in stores:
        structured_tables: list[tuple[str, str]] = []
    elif "graph" in stores and bases["structured"] == bases["graph"]:
        structured_tables = list(project_tables)
    else:
        structured_tables, _ = _classify_structured_text_diff(vault_dir, bases["structured"], head, domains)
    # A projected table is restored too, so table2graph reads DuckDB at
    # `head` whatever the structured bookmark says (idempotent).
    restore_tables = project_tables + [key for key in structured_tables if key not in project_tables]

    if dry_run:
        structured_only_dry: list[str] = []
        if project_tables:
            from artmind.table2graph import MappingError, find_mappings
            from artmind.temporal import load_schema
            import paths

            with tempfile.TemporaryDirectory(prefix="artmind-sync-") as scratch_str:
                dry_scratch = Path(scratch_str)
                mappings_at_head = _dir_at(vault_dir, head, paths.TABLE_MAPPINGS_DIR, dry_scratch)
                schemas_at_head = _dir_at(vault_dir, head, paths.DOMAIN_SCHEMAS_DIR, dry_scratch)
                for domain, table_name in project_tables:
                    try:
                        found = find_mappings(table_name, domain, mappings_dir=mappings_at_head)
                    except MappingError as e:
                        raise VaultSyncError(_vault_relative_mapping_error(e, dry_scratch, head)) from None
                    if len(found) > 1:
                        raise VaultSyncError(f"{domain}/{table_name}: ambiguous, {len(found)} mappings match")
                    if not found:
                        structured_only_dry.append(f"{domain}/{table_name}")
                        continue
                    # Same check the real run makes (below, `table_to_graph`'s
                    # caller): a mapped table whose domain schema was deleted
                    # must fail the dry run too, not just the real one (item 4).
                    if not load_schema(domain, schemas_dir=schemas_at_head):
                        raise VaultSyncError(f"{domain}/{table_name}: no schema for domain {domain!r}")

        return {
            "dry_run": True,
            "stores": list(stores),
            "bases": bases,
            "head": head,
            "replay": len(plan.replay_docs),
            "unchanged": len(unchanged),
            "retract": len(plan.retract),
            "regenerate_tables": len(restore_tables),
            "structured_only_tables": structured_only_dry,
            "same_as_groups": plan.same_as_groups,
            "same_as_keys": len(plan.same_as_keys),
            "curation": _curation_counts(plan.curation),
            "curation_unchanged": curation_unchanged,
            "cursor_would_advance": not domains,
        }

    from artmind import ingest
    from artmind.structured import registry as structured_registry
    from artmind.structured.text_export import MANIFEST_NAME, META_SUFFIX, import_structured_text
    from artmind.table2graph import MappingError, find_mappings, table_to_graph, _rebuild_in_batches
    from artmind.temporal import load_schema
    import paths
    from paths import KG_DIR, STRUCTURED_TEXT_DIR

    all_keys: set[tuple[str, str, str]] = set()
    structured_only: list[str] = []  # tables restored to DuckDB, not projected (§14 A2)
    chat_domains: set[str] = set()   # domains of replayed `update__*` folders (a UserChat each)

    with tempfile.TemporaryDirectory(prefix="artmind-sync-") as scratch_str:
        scratch = Path(scratch_str)

        # ── track B: regenerate (§5 step 2) -- BEFORE track A, so its output
        #    (freshly regenerated table__* JSON in the working tree) can never
        #    itself be mistaken for a track-A input in this same run (the
        #    diff_range above is already fixed from `plan`). Inputs come from
        #    `head`, never the live structured_text dir.
        if restore_tables:
            st_rel = STRUCTURED_TEXT_DIR.relative_to(vault_dir)
            wanted = [str(st_rel / MANIFEST_NAME)]
            for domain, table_name in restore_tables:
                wanted.append(str(st_rel / domain / f"{table_name}.csv"))
                wanted.append(str(st_rel / domain / f"{table_name}{META_SUFFIX}"))
            _materialize(vault_dir, head, _present_at(vault_dir, head, wanted), scratch)
            (scratch / st_rel).mkdir(parents=True, exist_ok=True)
            import_structured_text(scratch / st_rel, tables=restore_tables)
        if project_tables:
            mappings_at_head = _dir_at(vault_dir, head, paths.TABLE_MAPPINGS_DIR, scratch)
            schemas_at_head = _dir_at(vault_dir, head, paths.DOMAIN_SCHEMAS_DIR, scratch)
            for domain, table_name in project_tables:
                row = structured_registry.get_table(table_name, domain=domain)
                if row is None:
                    raise VaultSyncError(f"{domain}/{table_name}: not found in the registry after restore")
                try:
                    found = find_mappings(table_name, domain, mappings_dir=mappings_at_head)
                except MappingError as e:
                    raise VaultSyncError(_vault_relative_mapping_error(e, scratch, head)) from None
                if not found:
                    # Spec 2026-09-26 §14 A2: a table no mapping names is a
                    # structured-store table only -- restored above, never
                    # projected. Raising here stalled the cursor forever.
                    structured_only.append(f"{domain}/{table_name}")
                    continue
                if len(found) > 1:
                    raise VaultSyncError(f"{domain}/{table_name}: ambiguous, {len(found)} mappings match")
                schema = load_schema(domain, schemas_dir=schemas_at_head)
                if not schema:
                    raise VaultSyncError(f"{domain}/{table_name}: no schema for domain {domain!r}")
                report = table_to_graph(row, found[0], schema=schema, embed=False, defer_rebuild=True)
                all_keys.update(tuple(k) for k in report["commit"].get("deferred_keys") or [])

        # ── track A: replay (§5 step 3), from `head` ─────────────────────────
        #    Guarded by `if`: some tests patch only STRUCTURED_TEXT_DIR, leaving
        #    KG_DIR outside `vault_dir` (same reason as the ValueError guards in
        #    the classifiers), and relative_to would raise on an empty plan.
        if plan.replay_docs:
            kg_rel = KG_DIR.relative_to(vault_dir)
            _materialize(
                vault_dir, head,
                [str(kg_rel / domain / docdir) for domain, docdir in plan.replay_docs],
                scratch,
            )
            for domain, docdir in plan.replay_docs:
                snap_dir = scratch / kg_rel / domain / docdir
                ingest.carry_embedding_sidecar(KG_DIR / domain / docdir, snap_dir)
                summary = ingest._write_to_neo4j(snap_dir, domain, defer_rebuild=True)
                if summary is None:
                    raise VaultSyncError(f"{kg_rel / domain / docdir}: staged KG JSON at {head} could not be read")
                all_keys.update(tuple(k) for k in summary.get("deferred_keys") or [])
                if docdir.startswith(ingest.UPDATE_FOLDER_PREFIX):
                    chat_domains.add(domain)

    for domain, doc_id in plan.retract:
        result = ingest.retract_document(doc_id, domain)
        all_keys.update(tuple(k) for k in result.get("affected_keys") or [])

    # ── track D: every key of a same-as group added/removed/changed ─────────
    all_keys.update(tuple(k) for k in plan.same_as_keys)

    # ── track C, "pre" kinds: curation the rebuild must see ──────────────────
    all_keys.update(_apply_curation(vault_dir, plan.base, head, plan.curation, "pre"))

    # ── the union rebuild (§5 step 4), chunked exactly like table2graph's own ─
    #    With the same-as groups as committed at `head`, never the working
    #    tree's `same_as.yaml`; and every group touching a key is rebuilt
    #    whole (`projection.affected_keys`' set 3), which `projection.rebuild`
    #    requires of its caller.
    if all_keys:
        from artmind import projection

        head_groups = same_as_groups_at(vault_dir, head)
        all_keys = projection.affected_keys(incoming=sorted(all_keys), same_as_groups=head_groups)
        projection_summary = _rebuild_in_batches(sorted(all_keys), groups=head_groups)
    else:
        projection_summary = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": 0}

    # ── track C, "post" kinds: curation that joins rebuilt entities ─────────
    _apply_curation(vault_dir, plan.base, head, plan.curation, "post")

    # ── domain-scoped embed sweeps (§5 step 5) ──────────────────────────────
    touched_domains = sorted({k[2] for k in all_keys})
    for d in touched_domains:
        domain_keys = [k for k in all_keys if k[2] == d]
        ingest._sweep_embeddings(d, domain_keys)
        ingest._sweep_chunk_embeddings(domain=d)
    # A replayed `artmind update` is a UserChat, whose vector is never staged.
    for d in sorted(chat_domains):
        from artmind.update import embed_user_chats

        embed_user_chats(domain=d)

    # artmind never commits (spec 2026-09-26, D1): track B's regenerated
    # table__* folders stay in the working tree. (gitignored: spec 2026-09-26 R4)

    # ── only on full success, advance the bookmarks (§5 step 7) ─────────────
    # A `--domain`-scoped run only classified and applied ITS domain(s) --
    # advancing a bookmark anyway would make every other domain's change in
    # base..head permanently unapplied (item 3, vault-sync-completion review):
    # the next unscoped sync would start its diff from `head`, never seeing
    # them. Leave both where they were so a later unscoped sync still covers
    # this same range; re-applying this domain's changes then is a
    # no-op-shaped replay, not a problem (track A/B/§7 are idempotent by id/key).
    cursor_advanced = not domains
    final = _advance_bookmarks(vault_dir, vault_id, stores, head, marks) if cursor_advanced else marks

    result = {
        "stores": list(stores),
        "bases": bases,
        "head": head,
        **_bookmark_fields(final, stores),
        "cursor_advanced": cursor_advanced,
        "replayed": len(plan.replay_docs),
        "unchanged": len(unchanged),
        "retracted": len(plan.retract),
        "regenerated_tables": len(restore_tables),
        "structured_only_tables": structured_only,
        "same_as_groups": plan.same_as_groups,
        "same_as_keys": len(plan.same_as_keys),
        "curation": _curation_counts(plan.curation),
        "curation_unchanged": curation_unchanged,
        "projection": projection_summary,
        "domains_swept": touched_domains,
    }
    if not cursor_advanced:
        # A store with no bookmark at the START of this run only got past the
        # "no bookmark recorded yet" check because --bootstrapEmpty was
        # passed. An unscoped follow-up with no flag would hit that same check
        # and refuse, so the note must say to repeat --bootstrapEmpty, not just
        # "a later sync" (re-review item 3).
        follow_up = (
            "re-run `vault sync --bootstrapEmpty` (unscoped) to apply them"
            if any(marks.effective(name) is None for name in stores)
            else "a later unscoped `vault sync` will apply them (and idempotently re-apply this one)"
        )
        result["note"] = (
            f"domain-scoped sync (--domain {','.join(domains)}): applied but did not advance "
            f"the {' or '.join(stores)} bookmark -- other domains changed in this range are "
            f"still pending; {follow_up}"
        )
    return result


# ── pending work and the staleness warning (spec 2026-09-26 §6 A4) ───────────

#: Seconds an advisory graph read may spend connecting -- the staleness check
#: that rides along with every `query` command, and `vault status` -- so an
#: unreachable graph never holds a command up.
ADVISORY_TIMEOUT = 2.0


def _store_pending(
    vault_dir: Path, store: str, bookmark: str | None, head: str, *, timeout: float | None
) -> dict:
    """`{"bookmark", "state", "docs", "tables", "curation", "same_as_groups",
    "detail"}` for one store. `state` is `current`, `behind`, `no_bookmark`,
    `not_ancestor` (the bookmark is not in HEAD's history: pull first) or
    `error` (the range cannot be classified -- e.g. a table mapping broken at
    HEAD; `detail` says why, and `vault sync` would refuse with the same
    message). `docs` counts documents -- `artmind update` folders included --
    still to replay or retract after the fingerprint check (graph only);
    `tables` lists `(domain, table)` still to restore/project; `curation`
    counts curation records still to apply or remove after their own
    fingerprint check, and `same_as_groups` the same-as groups changed in the
    range (graph only)."""
    report = {
        "bookmark": bookmark, "state": "current", "docs": 0, "tables": [],
        "curation": 0, "same_as_groups": 0, "detail": None,
    }
    if bookmark is None:
        report["state"] = "no_bookmark"
        return report
    if bookmark == head:
        return report
    try:
        check_bookmark(vault_dir, store, bookmark, head)
    except VaultSyncError as e:
        report.update(state="not_ancestor", detail=str(e))
        return report
    from neo4j.exceptions import GqlError

    try:
        if store == "structured":
            tables, _ = _classify_structured_text_diff(vault_dir, bookmark, head, None)
            docs = curation = same_as_groups = 0
        else:
            docs, tables, curation, same_as_groups = _graph_pending(vault_dir, bookmark, head, timeout=timeout)
    except VaultSyncError as e:
        report.update(state="error", detail=str(e))
        return report
    except (GqlError, OSError) as e:
        # The bookmark read (in `read_bookmarks`, `status_report`'s own catch)
        # can succeed and the graph still go unreachable by the time this
        # later fingerprint read runs -- a real race, not a hypothetical one.
        # Unguarded, that would break `status_report`'s "never raises for an
        # unreachable graph" promise. Same exception scoping as
        # `status_report`'s bookmark-read catch: only what the graph-read
        # path can legitimately raise, so a genuine bug still crashes.
        report.update(state="error", detail=f"graph unreachable: {e}")
        return report
    behind = docs or tables or curation or same_as_groups
    report.update(
        state="behind" if behind else "current", docs=docs, tables=sorted(tables),
        curation=curation, same_as_groups=same_as_groups,
    )
    return report


def _graph_pending(
    vault_dir: Path, bookmark: str, head: str, *, timeout: float | None
) -> tuple[int, set[tuple[str, str]], int, int]:
    """`(docs, tables, curation, same_as_groups)` the graph still has to
    apply between `bookmark` and `head`: a replay counts unless the graph
    already carries its committed fingerprint; a retraction counts only while
    the graph still has the document. ONE Cypher read covers both. Curation
    records are counted the same way (one read per kind in the range), and
    every same-as group changed in the range counts -- the graph keeps no
    per-group fingerprint to compare against."""
    from artmind import sync_state

    plan = classify_diff(vault_dir, bookmark, head)
    committed = committed_fingerprints(vault_dir, head, plan.replay_docs)
    ids = [doc_id for doc_id, fp in committed.values() if doc_id is not None and fp is not None]
    ids += [doc_id for _, doc_id in plan.retract]
    in_graph = sync_state.read_document_fingerprints(ids, timeout=timeout) if ids else {}
    replay = [key for key in plan.replay_docs if not _is_unchanged(committed.get(key), in_graph)]
    retract = [doc_id for _, doc_id in plan.retract if doc_id in in_graph]
    tables = set(plan.regenerate_tables)
    tables |= {tuple(doc_id.split(":", 2)[1:]) for doc_id in retract if doc_id.startswith("table:")}
    docs = len(replay) + sum(1 for doc_id in retract if not doc_id.startswith("table:"))
    curation = 0
    if plan.curation:
        committed_records = committed_curation_fingerprints(vault_dir, head, plan)
        held = _graph_curation_fingerprints(plan.curation, timeout=timeout)
        curation = sum(
            1 for kind, record_id, action in plan.curation
            if (action == "apply" and held.get((kind, record_id)) != committed_records.get((kind, record_id)))
            or (action == "remove" and (kind, record_id) in held)
        )
    return docs, tables, curation, plan.same_as_groups


def pending_work(
    vault_dir: Path, head: str, marks: Bookmarks, *, stores: tuple[str, ...] = STORES,
    timeout: float | None = ADVISORY_TIMEOUT,
) -> dict:
    """What `vault sync` would still apply, per store: `{store: {...}}` as
    `_store_pending` reports it. Reads git (names and committed blobs only)
    and, for the graph, one Cypher read of the affected documents'
    fingerprints."""
    return {
        store: _store_pending(vault_dir, store, marks.effective(store), head, timeout=timeout)
        for store in stores
    }


def _one_line(detail: object) -> str:
    """Collapse `detail` to a single line for splicing into the one advisory
    stderr line -- a multi-line PyYAML error (header, `in "...", line N,
    column M:`, source snippet, `^` caret) would otherwise break that
    contract. Whitespace runs, including newlines, collapse to single spaces."""
    return " ".join(str(detail).split())


def curation_phrase(report: dict) -> str:
    """` / K curation records / G same-as groups` for a graph store's
    pending report -- each part only when non-zero, so the line reads as
    before when no curation is pending."""
    parts = []
    if report.get("curation"):
        parts.append(f"{report['curation']} curation records")
    if report.get("same_as_groups"):
        parts.append(f"{report['same_as_groups']} same-as groups")
    return "".join(f" / {part}" for part in parts)


def staleness_message(pending: dict) -> str | None:
    """The one advisory line for stderr, or None when nothing is pending.
    `M tables` counts every table either store still has to apply -- a table
    restored into DuckDB is also what the graph projects. Pending curation
    records and same-as groups follow, when there are any."""
    graph = pending.get("graph", {})
    structured = pending.get("structured", {})
    for name, report in (("graph", graph), ("structured", structured)):
        if report.get("state") == "not_ancestor":
            return (
                f"artmind: the {name} store's sync bookmark is not in this clone's history "
                "-- let Obsidian Git pull, then run `artmind vault sync`"
            )
    erroring = [name for name, report in (("graph", graph), ("structured", structured)) if report.get("state") == "error"]
    if len(erroring) == 1:
        name = erroring[0]
        detail = _one_line(pending[name].get("detail"))
        return (
            f"artmind: the {name} store's sync would fail -- {detail} -- "
            "fix it, then run `artmind vault sync`"
        )
    if erroring:
        details = "; ".join(f"{name}: {_one_line(pending[name].get('detail'))}" for name in erroring)
        return (
            f"artmind: the {' and '.join(erroring)} stores' sync would fail -- {details} -- "
            "fix it, then run `artmind vault sync`"
        )
    tables = {tuple(t) for t in graph.get("tables", [])} | {tuple(t) for t in structured.get("tables", [])}
    docs = graph.get("docs", 0)
    extra = curation_phrase(graph)
    if not docs and not tables and not extra:
        return None
    if not docs and not graph.get("tables") and not extra:
        return f"artmind: structured store is {len(tables)} tables behind the vault — run `artmind vault sync`"
    return f"artmind: graph is {docs} docs / {len(tables)} tables{extra} behind the vault — run `artmind vault sync`"


def query_staleness_warning(vault_dir: Path | None) -> str | None:
    """The staleness line a `query` command prints on stderr, or None.

    Cheap by construction, in this order, stopping at the first "nothing to
    say": no vault; this machine has never run `vault sync` (no bookmark in
    `state.json` -- a local file read, so a single-machine user never pays
    for a graph round trip); no vault_id; `git rev-parse HEAD` fails (no git,
    no commits); both bookmarks equal HEAD (one Cypher read, capped at
    `ADVISORY_TIMEOUT`). Only then: git names/blobs and one fingerprint read.
    Raises whatever those raise -- the caller swallows every exception, so a
    query is never broken by its own warning."""
    from artmind.manifest import read_vault_id
    from artmind.vault import VaultLayout, read_state

    if vault_dir is None:
        return None
    state = read_state(VaultLayout(vault_dir))
    if STRUCTURED_BOOKMARK_KEY not in state and LEGACY_CURSOR_KEY not in state:
        return None
    vault_id = read_vault_id(vault_dir)
    if vault_id is None:
        return None
    rc, out, _ = run_command(["git", "rev-parse", "HEAD"], cwd=vault_dir, expected_codes=(128,))
    if rc != 0:
        return None
    head = out.strip()
    marks = read_bookmarks(vault_dir, vault_id=vault_id, timeout=ADVISORY_TIMEOUT)
    if all(marks.effective(store) == head for store in STORES):
        return None
    return staleness_message(pending_work(vault_dir, head, marks))


def status_report(vault_dir: Path) -> dict:
    """Everything `vault status` shows about sync. Never raises for an
    unreachable graph, a repo with no commits, or a vault with no vault_id:
    each becomes a field the caller prints."""
    from artmind.manifest import ManifestError, read_vault_id

    rc, out, _ = run_command(["git", "rev-parse", "HEAD"], cwd=vault_dir, expected_codes=(128,))
    head = out.strip() if rc == 0 else None
    report: dict = {
        "head": head,
        "vault_id": None,
        "operation_in_progress": None,
        "unresolved_conflicts": [],
        "legacy_cursor": None,
        "graph_error": None,
        "stores": {},
        "message": None,
    }
    try:
        report["operation_in_progress"] = operation_in_progress(vault_dir)
        report["unresolved_conflicts"] = unresolved_artmind_conflicts(vault_dir)
    except VaultSyncError as e:
        # `_git` raises `VaultSyncError` for ANY nonzero exit from ANY git
        # subcommand it wraps (a missing repo, a lock file, a corrupted
        # `.git`, a permissions issue, ...) -- `str(e)` is already the real
        # `git ... failed: <stderr>` message, so it is shown as-is rather
        # than guessing a specific cause ("not a git repository") that may
        # not be what actually happened.
        report["graph_error"] = str(e)
        return report
    try:
        report["vault_id"] = read_vault_id(vault_dir)
    except ManifestError as e:
        report["graph_error"] = str(e)

    stores: tuple[str, ...] = STORES
    if report["vault_id"] is None:
        stores = ("structured",)
        report["graph_error"] = report["graph_error"] or "no vault_id in .artmind/vault.yaml -- run `artmind init`"
    from neo4j.exceptions import GqlError

    try:
        marks = read_bookmarks(vault_dir, vault_id=report["vault_id"], stores=stores, timeout=ADVISORY_TIMEOUT)
    except (GqlError, OSError) as e:
        # An unreachable graph is a status, not a crash -- but only for
        # exceptions the graph-read path (`read_bookmarks` ->
        # `sync_state.read_graph_bookmark` -> `graph_query.neo4j_session` ->
        # the neo4j driver) can actually raise: every neo4j-specific
        # exception is a `GqlError` (its `Neo4jError`/`DriverError`
        # subclasses cover server errors, `ServiceUnavailable`,
        # `SessionExpired`, auth/config/pool errors, ...), and low-level
        # connect failures (including a `connection_timeout` expiring) show
        # up as `OSError` (or its `TimeoutError` subclass). A genuine bug
        # elsewhere in this path -- an `AttributeError`/`TypeError` from a
        # refactor -- must still crash instead of being relabeled
        # "graph unreachable", which would hide exactly the kind of defect
        # a user checking `vault status` is trying to diagnose.
        report["graph_error"] = f"graph unreachable: {e}"
        stores = ("structured",)
        marks = read_bookmarks(vault_dir, vault_id=None, stores=stores)
    report["legacy_cursor"] = marks.legacy
    if head is None:
        report["stores"] = {s: {"bookmark": marks.effective(s), "state": "no_commits"} for s in stores}
        return report
    report["stores"] = pending_work(vault_dir, head, marks, stores=stores)
    report["message"] = staleness_message(report["stores"])
    return report
