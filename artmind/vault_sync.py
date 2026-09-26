"""`vault sync`: git-diff-driven, CDC-style replay into Neo4j and the
structured store (docs/superpowers/specs/2026-09-25-vault-sync-design.md).

Detects exactly which committed KG-staging document folders
(`.artmind/data/kg/**`) and committed structured-store text
(`.artmind/data/structured_text/**`) changed since the last sync, and
replays only those changes -- incremental and git-native, complementing
(not replacing) the whole-graph `session close`/`session initiate` snapshot.
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from utils.functions import run_command

#: The git object id of the canonical empty tree, present in every
#: repository -- `git diff <this> HEAD` is the standard way to diff "against
#: nothing", which is exactly `--bootstrapEmpty`'s "everything currently
#: committed counts as added" semantics, without special-casing a repo's own
#: (possibly multiple) root commit(s).
EMPTY_TREE_SHA = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


class VaultSyncError(Exception):
    """A `vault sync` run cannot proceed -- refused before writing anything."""


def _git(vault_dir: Path, args: str) -> str:
    rc, out, err = run_command(f"git {args}", cwd=vault_dir)
    if rc != 0:
        raise VaultSyncError(f"git {args} failed: {err or out}")
    return out


def head_sha(vault_dir: Path) -> str:
    """The vault's current commit sha. Raises a clear, specific
    `VaultSyncError` (not `_git`'s generic wrapper) when there are no
    commits yet -- exactly the state `artmind init` leaves a fresh vault in
    (it runs `git init`, never a commit), and the one case every `sync()`
    call needs a real, named message for, since bootstrap or not, dry-run or
    not, there is nothing to sync/stamp without a HEAD to point at."""
    rc, out, err = run_command("git rev-parse HEAD", cwd=vault_dir, expected_codes=(128,))
    if rc != 0:
        raise VaultSyncError(
            "this vault's git repo has no commits yet -- nothing to sync. "
            "Commit something to it first, then run `vault sync` again."
        )
    return out.strip()


def _diff_name_status(vault_dir: Path, base: str, head: str, scope: Path) -> list[tuple[str, str]]:
    """`[(status, path), ...]` for `scope` between `base` and `head`, paths
    relative to `vault_dir`. `status` is git's own single-letter code
    (A/M/D); `--no-renames` guarantees a delete+add pair rather than a
    single "R100" line regardless of the user's global git config, which
    the per-path classification in `_classify_kg_diff`/
    `_classify_structured_text_diff` depends on."""
    rel_scope = scope.relative_to(vault_dir)
    out = _git(vault_dir, f'diff --no-renames --name-status {base} {head} -- "{rel_scope}"')
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
    rc, _, _ = run_command(f'git cat-file -e "{rev}^{{commit}}"', cwd=vault_dir, expected_codes=(128,))
    if rc != 0:
        raise VaultSyncError(f"{rev!r} does not resolve to a commit in this vault's git history")
    rc, out, _ = run_command(f'git show "{rev}:{relpath}"', cwd=vault_dir, expected_codes=(128,))
    return out if rc == 0 else None


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
        # `paths.KG_DIR` isn't inside this vault_dir at all (e.g. a
        # track-B-only caller/test that never pointed it at this vault) --
        # nothing in this track to report, rather than a hard failure.
        return [], []
    try:
        kg_rel = kg_dir.relative_to(vault_dir)
    except ValueError:
        kg_rel = Path(".")

    replay: list[tuple[str, str]] = []
    retract: list[tuple[str, str]] = []
    for status, path in rows:
        try:
            rel = Path(path).relative_to(kg_rel)
        except ValueError:
            continue
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
    """Track B (spec §4.B): regenerate/retract for every table's CSV under
    `.artmind/data/structured_text/**`. `manifest.json` is a single shared
    file (not "under a given table's export"); a manifest-only change with
    no accompanying CSV diff triggers nothing in this version -- a
    documented scoping decision, not an oversight."""
    import paths

    st_dir = paths.STRUCTURED_TEXT_DIR
    try:
        rows = _diff_name_status(vault_dir, base, head, st_dir)
    except ValueError:
        # Symmetric with `_classify_kg_diff`: `paths.STRUCTURED_TEXT_DIR`
        # isn't inside this vault_dir -- nothing in this track to report.
        return [], []
    try:
        st_rel = st_dir.relative_to(vault_dir)
    except ValueError:
        st_rel = Path(".")

    regenerate: list[tuple[str, str]] = []
    retract: list[tuple[str, str]] = []
    for status, path in rows:
        try:
            rel = Path(path).relative_to(st_rel)
        except ValueError:
            continue
        parts = rel.parts
        if len(parts) != 2 or not parts[1].endswith(".csv"):
            continue
        domain, filename = parts
        table_name = filename[: -len(".csv")]
        if not _in_scope(domain, domains):
            continue
        if status in ("A", "M"):
            regenerate.append((domain, table_name))
        elif status == "D":
            retract.append((domain, f"table:{domain}:{table_name}"))
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
