"""`vault sync`: git-diff-driven, CDC-style replay into Neo4j and the
structured store (docs/superpowers/specs/2026-09-25-vault-sync-design.md).

Detects exactly which committed KG-staging document folders
(`.artmind/data/kg/**`) and committed structured-store text
(`.artmind/data/structured_text/**`) changed since the last sync, and
replays only those changes -- incremental and git-native, complementing
(not replacing) the whole-graph `session close`/`session initiate` snapshot.

Applies committed content only: every input is materialised from the fixed
`head` via `git archive`, never read from the working tree, and `sync` never
commits (spec 2026-09-26-vault-git-transport-design.md, §6 A1, D1).
"""
from __future__ import annotations

import io
import shutil
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


def _worker_running() -> bool:
    """Whether this vault's ingest worker is alive (its pid file names a live
    process). The worker writes the same graph keys `sync` replays, and its
    staging writes are what `sync` would be reading."""
    import os

    import paths

    try:
        pid = int(paths.WORKER_PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # alive, owned by someone else
    return True


def preflight(vault_dir: Path) -> None:
    """Refuse -- before reading or writing anything -- when the vault is in a
    state `sync` must not apply from (spec 2026-09-26 §6 A5, §14 A7): a merge,
    rebase or cherry-pick in progress, unresolved conflicts under `.artmind/`,
    or a running ingest worker. Each message says what to do next."""
    git_dir = Path(_git(vault_dir, ["rev-parse", "--absolute-git-dir"]).strip())
    for marker, what in (
        ("MERGE_HEAD", "a merge"),
        ("rebase-merge", "a rebase"),
        ("rebase-apply", "a rebase"),
        ("CHERRY_PICK_HEAD", "a cherry-pick"),
    ):
        if (git_dir / marker).exists():
            raise VaultSyncError(
                f"{what} is in progress in this vault -- finish it in Obsidian "
                "(or git) first, then re-run `vault sync`"
            )
    # Only artmind's own files (spec §14 A7): a conflicted human note does not
    # change what sync applies, and Obsidian shows it to the user. `-z` keeps a
    # name with a space (or a non-ASCII byte) whole (§14 A5).
    unmerged = [
        p for p in _git(vault_dir, ["diff", "--name-only", "-z", "--diff-filter=U", "--", MARKER]).split("\0") if p
    ]
    if unmerged:
        raise VaultSyncError(
            f"unresolved conflicts in {len(unmerged)} file(s) under {MARKER}/ ({', '.join(unmerged[:5])}"
            f"{', ...' if len(unmerged) > 5 else ''}) -- resolve them first"
        )
    if _worker_running():
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


def _carry_sidecar(live_dir: Path, snap_dir: Path) -> None:
    """Copy the gitignored embedding sidecar from the live staging folder into
    its HEAD snapshot -- only when the live `chunks.json` is byte-identical to
    the committed one. The sidecar's vectors belong to the on-disk chunks;
    attaching them to different committed chunks would pair a chunk id with
    the wrong vector. Without it, the chunk embed sweep recomputes locally."""
    from artmind.ingest import EMBEDDING_SIDECAR

    sidecar = live_dir / EMBEDDING_SIDECAR
    live_chunks = live_dir / "chunks.json"
    snap_chunks = snap_dir / "chunks.json"
    if not (sidecar.is_file() and live_chunks.is_file() and snap_chunks.is_file()):
        return
    if live_chunks.read_bytes() == snap_chunks.read_bytes():
        shutil.copy2(sidecar, snap_dir / EMBEDDING_SIDECAR)


from dataclasses import dataclass, field


@dataclass
class SyncPlan:
    base: str
    head: str
    replay_docs: list[tuple[str, str]] = field(default_factory=list)        # (domain, docdir)
    retract: list[tuple[str, str]] = field(default_factory=list)            # (domain, doc_id)
    regenerate_tables: list[tuple[str, str]] = field(default_factory=list)  # (domain, table_name)


def _in_scope(domain: str, domains: list[str] | None) -> bool:
    if not domains:
        return True
    return any(domain == d or domain.startswith(d + ".") for d in domains)


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
    retract: list[tuple[str, str]] = []
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
            retract.append((domain, json.loads(base_content)["id"]))
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


def classify_diff(
    vault_dir: Path, layout, base: str, head: str, domains: list[str] | None = None
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
    return plan


def sync(
    vault_dir: Path,
    *,
    domains: list[str] | None = None,
    bootstrap_empty: bool = False,
    bootstrap_synced: bool = False,
    dry_run: bool = False,
) -> dict:
    """Replay committed KG-staging and structured-text changes into
    Neo4j/DuckDB since the last sync (spec §5). All-or-nothing: the cursor
    only advances on full success (§9); any exception here propagates
    unchanged, leaving `last_synced_commit` untouched so the next
    invocation recomputes the identical diff_range from scratch.
    """
    from artmind.vault import VaultLayout, read_state, write_state

    if bootstrap_empty and bootstrap_synced:
        raise VaultSyncError("pass at most one of --bootstrapEmpty / --bootstrapSynced")

    layout = VaultLayout(vault_dir)
    state = read_state(layout)
    last_synced = state.get("last_synced_commit")
    head = head_sha(vault_dir)
    preflight(vault_dir)

    if bootstrap_synced:
        if dry_run:
            return {"bootstrap": "synced", "dry_run": True, "would_stamp": head}
        write_state(layout, {"last_synced_commit": head})
        return {"bootstrap": "synced", "last_synced_commit": head}

    if last_synced is None and not bootstrap_empty:
        raise VaultSyncError(
            "no last_synced_commit recorded yet -- pass --bootstrapEmpty for a full "
            "replay of everything (correct for an empty Neo4j/DuckDB), or "
            "--bootstrapSynced if this machine's graph is already known-current "
            "(e.g. right after `session initiate`/`db restore`)"
        )
    base = EMPTY_TREE_SHA if bootstrap_empty else last_synced

    plan = classify_diff(vault_dir, layout, base, head, domains)

    if dry_run:
        structured_only_dry: list[str] = []
        if plan.regenerate_tables:
            from artmind.table2graph import MappingError, find_mappings
            import paths

            with tempfile.TemporaryDirectory(prefix="artmind-sync-") as scratch_str:
                dry_scratch = Path(scratch_str)
                mappings_at_head = _dir_at(vault_dir, head, paths.TABLE_MAPPINGS_DIR, dry_scratch)
                for domain, table_name in plan.regenerate_tables:
                    try:
                        found = find_mappings(table_name, domain, mappings_dir=mappings_at_head)
                    except MappingError as e:
                        raise VaultSyncError(_vault_relative_mapping_error(e, dry_scratch, head)) from None
                    if len(found) > 1:
                        raise VaultSyncError(f"{domain}/{table_name}: ambiguous, {len(found)} mappings match")
                    if not found:
                        structured_only_dry.append(f"{domain}/{table_name}")

        return {
            "dry_run": True,
            "base": base,
            "head": head,
            "replay": len(plan.replay_docs),
            "retract": len(plan.retract),
            "regenerate_tables": len(plan.regenerate_tables),
            "structured_only_tables": structured_only_dry,
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

    with tempfile.TemporaryDirectory(prefix="artmind-sync-") as scratch_str:
        scratch = Path(scratch_str)

        # ── track B: regenerate (§5 step 2) -- BEFORE track A, so its output
        #    (freshly regenerated table__* JSON in the working tree) can never
        #    itself be mistaken for a track-A input in this same run (the
        #    diff_range above is already fixed from `plan`). Inputs come from
        #    `head`, never the live structured_text dir.
        if plan.regenerate_tables:
            st_rel = STRUCTURED_TEXT_DIR.relative_to(vault_dir)
            wanted = [str(st_rel / MANIFEST_NAME)]
            for domain, table_name in plan.regenerate_tables:
                wanted.append(str(st_rel / domain / f"{table_name}.csv"))
                wanted.append(str(st_rel / domain / f"{table_name}{META_SUFFIX}"))
            _materialize(vault_dir, head, _present_at(vault_dir, head, wanted), scratch)
            (scratch / st_rel).mkdir(parents=True, exist_ok=True)
            import_structured_text(scratch / st_rel, tables=plan.regenerate_tables)
            mappings_at_head = _dir_at(vault_dir, head, paths.TABLE_MAPPINGS_DIR, scratch)
            schemas_at_head = _dir_at(vault_dir, head, paths.DOMAIN_SCHEMAS_DIR, scratch)
            for domain, table_name in plan.regenerate_tables:
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
                _carry_sidecar(KG_DIR / domain / docdir, snap_dir)
                summary = ingest._write_to_neo4j(snap_dir, domain, defer_rebuild=True)
                if summary is None:
                    raise VaultSyncError(f"{kg_rel / domain / docdir}: staged KG JSON at {head} could not be read")
                all_keys.update(tuple(k) for k in summary.get("deferred_keys") or [])

    for domain, doc_id in plan.retract:
        result = ingest.retract_document(doc_id, domain)
        all_keys.update(tuple(k) for k in result.get("affected_keys") or [])

    # ── the union rebuild (§5 step 4), chunked exactly like table2graph's own ─
    if all_keys:
        projection_summary = _rebuild_in_batches(sorted(all_keys))
    else:
        projection_summary = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0, "batches": 0}

    # ── domain-scoped embed sweeps (§5 step 5) ──────────────────────────────
    touched_domains = sorted({k[2] for k in all_keys})
    for d in touched_domains:
        domain_keys = [k for k in all_keys if k[2] == d]
        ingest._sweep_embeddings(d, domain_keys)
        ingest._sweep_chunk_embeddings(domain=d)

    # artmind never commits (spec 2026-09-26, D1): track B's regenerated
    # table__* folders stay in the working tree. (gitignored: spec 2026-09-26 R4)

    # ── only on full success, advance the cursor (§5 step 7) ────────────────
    write_state(layout, {"last_synced_commit": head})

    return {
        "base": base,
        "last_synced_commit": head,
        "replayed": len(plan.replay_docs),
        "retracted": len(plan.retract),
        "regenerated_tables": len(plan.regenerate_tables),
        "structured_only_tables": structured_only,
        "projection": projection_summary,
        "domains_swept": touched_domains,
    }
