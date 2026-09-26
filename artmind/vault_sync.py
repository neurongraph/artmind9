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
    state `sync` must not apply from (spec 2026-09-26 §6 A5): a merge, rebase
    or cherry-pick in progress, unresolved conflicts in the index, or a running
    ingest worker. Each message says what to do next."""
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
    unmerged = _git(vault_dir, ["diff", "--name-only", "--diff-filter=U"]).split()
    if unmerged:
        raise VaultSyncError(
            f"unresolved conflicts in {len(unmerged)} file(s) ({', '.join(unmerged[:5])}"
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
    `_classify_structured_text_diff` depends on."""
    rel_scope = scope.relative_to(vault_dir)
    out = _git(vault_dir, ["diff", "--no-renames", "--name-status", base, head, "--", str(rel_scope)])
    rows = []
    for line in out.splitlines():
        if not line.strip():
            continue
        status, _, path = line.partition("\t")
        rows.append((status[0], path))
    return rows


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
    `.meta.json` (or the legacy `manifest.json`) may legitimately be absent."""
    if not relpaths:
        return []
    present = set(_git(vault_dir, ["ls-tree", "-r", "--name-only", rev, "--", *relpaths]).splitlines())
    return [p for p in relpaths if p in present]


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
    """Track A (spec §4.A): replay/retract for every document (and
    table__<name>) folder under `.artmind/data/kg/**`. Driven entirely by
    git's own status letters for `observations.json` specifically -- never
    the live filesystem, which track B may already have written fresh,
    uncommitted content into by the time this runs in the same sync (spec
    §5's sequencing guarantee falls out of this for free, since `_show`
    below only ever reads committed history)."""
    import paths

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

    replay: list[tuple[str, str]] = []
    retract: list[tuple[str, str]] = []
    for status, path in rows:
        rel = Path(path).relative_to(kg_rel)
        parts = rel.parts
        if len(parts) != 3 or parts[2] != "observations.json":
            continue
        domain, docdir, _ = parts
        if not _in_scope(domain, domains):
            continue
        if status in ("A", "M"):
            replay.append((domain, docdir))
        elif status == "D":
            # The doc's id lives in the sibling `document.json`, not in
            # `observations.json` itself (which is a bare list) -- read that
            # file's content as it stood at `base`, right before removal.
            doc_json_path = str(Path(path).parent / "document.json")
            base_content = _show(vault_dir, base, doc_json_path)
            if base_content is None:
                raise VaultSyncError(
                    f"{path} is marked removed since {base}, but wasn't present at {base} "
                    "either -- diff and history disagree, refusing to guess"
                )
            import json
            doc_id = json.loads(base_content)["id"]
            retract.append((domain, doc_id))
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


def classify_diff(
    vault_dir: Path, layout, base: str, head: str, domains: list[str] | None = None
) -> SyncPlan:
    """The full classified diff for one sync run (spec §4), over the FIXED
    `base..head` range the caller has already resolved -- never re-queried
    mid-run, which is what makes §5's sequencing guarantee hold."""
    plan = SyncPlan(base=base, head=head)
    plan.replay_docs, kg_retract = _classify_kg_diff(vault_dir, base, head, domains)
    plan.regenerate_tables, table_retract = _classify_structured_text_diff(vault_dir, base, head, domains)

    seen: set[tuple[str, str]] = set()
    for domain, doc_id in kg_retract + table_retract:
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
        return {
            "dry_run": True,
            "base": base,
            "head": head,
            "replay": len(plan.replay_docs),
            "retract": len(plan.retract),
            "regenerate_tables": len(plan.regenerate_tables),
        }

    from artmind import ingest
    from artmind.structured import registry as structured_registry
    from artmind.structured.text_export import MANIFEST_NAME, META_SUFFIX, import_structured_text
    from artmind.table2graph import find_mappings, table_to_graph, _rebuild_in_batches
    from artmind.temporal import load_schema
    from paths import KG_DIR, STRUCTURED_TEXT_DIR

    all_keys: set[tuple[str, str, str]] = set()

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
            for domain, table_name in plan.regenerate_tables:
                row = structured_registry.get_table(table_name, domain=domain)
                if row is None:
                    raise VaultSyncError(f"{domain}/{table_name}: not found in the registry after restore")
                found = find_mappings(table_name, domain)
                if not found:
                    raise VaultSyncError(f"{domain}/{table_name}: no table mapping under domains/table_mappings/")
                if len(found) > 1:
                    raise VaultSyncError(f"{domain}/{table_name}: ambiguous, {len(found)} mappings match")
                schema = load_schema(domain)
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
        "projection": projection_summary,
        "domains_swept": touched_domains,
    }
