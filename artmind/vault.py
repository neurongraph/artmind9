"""Vault discovery and layout (docs/vault.md).

A **vault** is a directory whose `.artmind/` holds a `vault.yaml` manifest. It
is the user's Obsidian vault, their git repo and their artmind knowledge base at
once. You do not select a vault — you are standing in one, or you are not,
exactly as with a git repo.

The manifest, not the directory, is the marker. `~/.artmind` is also the
machine-wide config directory, so keying on the directory alone would make
`$HOME` itself a vault and every command run from anywhere beneath it would
resolve there — silently keying document identity off `$HOME`. Requiring the
manifest `artmind init` writes also means a half-created `.artmind/` is not
mistaken for a vault.

This module is deliberately pure and free of `paths` imports: `paths` imports
*this*, runs at import time for every command, and must stay cheap. Keeping
discovery here also makes it unit-testable without reimporting `paths`.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

# The directory artmind keeps everything in, and the manifest inside it that
# marks the containing directory as a vault. See the module docstring for why
# the manifest rather than the directory is the marker.
MARKER = ".artmind"
MANIFEST = "vault.yaml"


def is_vault(path: Path) -> bool:
    """Is `path` the root of an artmind vault?"""
    return (Path(path) / MARKER / MANIFEST).is_file()


def find_vault(start: Path) -> Path | None:
    """The innermost vault at or above `start`, or None.

    Walks up exactly as git walks up for `.git/`, so nested vaults behave like
    nested repos: the innermost wins.
    """
    try:
        current = Path(start).expanduser().resolve()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        if is_vault(candidate):
            return candidate
    return None


class VaultError(Exception):
    """A vault could not be resolved, or the one named does not exist."""


def resolve_vault(explicit: str | None = None) -> Path | None:
    """The active vault, or None when the caller is not inside one.

    Precedence, highest first (docs/vault.md, "Resolution"):

    1. ``explicit`` — the ``--vault`` flag
    2. ``ARTMIND_VAULT`` — for cron and anything with no meaningful cwd
    3. the walk up from the current directory

    An explicitly named vault that does not exist **raises** rather than
    falling back to the walk-up: silently ingesting into a different knowledge
    base than the one you named is the worst failure this system can have.
    """
    for value, source in ((explicit, "--vault"), (os.environ.get("ARTMIND_VAULT"), "ARTMIND_VAULT")):
        if not value:
            continue
        root = Path(value).expanduser().resolve()
        if not is_vault(root):
            raise VaultError(
                f"{source}={value!r} is not an artmind vault "
                f"({root / MARKER / MANIFEST} not found). "
                f"Run `artmind init` inside it to make it one."
            )
        return root
    return find_vault(Path.cwd())


@dataclass(frozen=True)
class VaultLayout:
    """Where everything sits inside a vault (docs/vault.md, "Layout").

    One place that knows the layout, so a path is never spelled out twice. The
    ownership rule: everything under ``artmind_dir`` (including ``data_dir``) is
    committed by default, minus the short exclusion list in ``GITIGNORE_BLOCK``
    below (a secret, the registry, the embedding sidecar, machine-local runtime
    state) -- see docs/vault.md, "What is in git, and what is not".
    """

    root: Path

    # ── artmind-owned, mostly committed (exceptions: GITIGNORE_BLOCK) ─────────
    @property
    def artmind_dir(self) -> Path:
        return self.root / MARKER

    @property
    def vault_yaml(self) -> Path:
        """The ingest manifest: folder->domain mapping and settings."""
        return self.artmind_dir / "vault.yaml"

    @property
    def same_as(self) -> Path:
        return self.artmind_dir / "same_as.yaml"

    @property
    def domains_dir(self) -> Path:
        return self.artmind_dir / "domains"

    @property
    def schemas_dir(self) -> Path:
        return self.domains_dir / "schemas"

    @property
    def meta_yaml(self) -> Path:
        return self.domains_dir / "meta.yaml"

    @property
    def inbox_dir(self) -> Path:
        """The drafting area `ingest.NEVER_WALKED` always skips, at any depth,
        even when named directly as the walk root (docs/vault.md, "Layout").
        Visible and committed: unfinished notes belong in the vault, not
        gitignored out of it."""
        return self.root / "_Inbox"

    @property
    def external_docs_dir(self) -> Path:
        """Copies of sources ingested from outside the vault, keyed by source
        path (not filename) so two different documents that happen to share a
        basename never collide (docs/vault.md, "Where a document lands").
        Visible and committed, NOT hidden: these are the vault's own record of
        what was ingested from elsewhere."""
        return self.root / "_external_docs"

    @property
    def skills_dir(self) -> Path:
        """`ClaudeAgentOptions.skills` takes names resolved from
        `.claude/skills/` relative to the agent's cwd, which is the vault."""
        return self.root / ".claude" / "skills"

    @property
    def opencode_agents_dir(self) -> Path:
        """opencode resolves ACP session modes (custom agents) from
        `.opencode/agent/*.md` relative to the `cwd` a session is opened
        with -- ARTMIND_AGENT_CWD, the vault root, same as `skills_dir`."""
        return self.root / ".opencode" / "agent"

    # ── machine-local, gitignored ────────────────────────────────────────────
    @property
    def config_env(self) -> Path:
        return self.artmind_dir / "config.env"

    @property
    def state_json(self) -> Path:
        """Machine-local cursors: `last_ingested_commit`, and `vault sync`'s
        structured-store bookmark `last_structured_commit` (the graph's lives
        in the graph)."""
        return self.artmind_dir / "state.json"

    @property
    def logs_dir(self) -> Path:
        return self.artmind_dir / "logs"

    @property
    def worker_pid(self) -> Path:
        """The ingest worker's pid file (`paths.WORKER_PID_FILE` inside this
        vault). Beside `state_json`, not under `data_dir`: the worker never
        unlinks it (item 1, vault-sync-completion review), so it must sit
        somewhere `GITIGNORE_BLOCK` actually covers -- `.artmind/worker.pid`,
        not `.artmind/data/worker.pid` -- or Obsidian Git would commit it and
        two machines would conflict on a file whose inode a pull can swap out
        from under the flock liveness check."""
        return self.artmind_dir / "worker.pid"

    # ── derived, committed (exceptions: registry_db, the kg embedding sidecar) ─
    @property
    def data_dir(self) -> Path:
        return self.artmind_dir / "data"

    @property
    def kg_dir(self) -> Path:
        return self.data_dir / "kg"

    @property
    def originals_dir(self) -> Path:
        """Only sources ingested from OUTSIDE the vault. A binary already in the
        vault is never copied (docs/stores-and-repos.md)."""
        return self.data_dir / "originals"

    @property
    def chunks_dir(self) -> Path:
        return self.data_dir / "chunks"

    @property
    def registry_db(self) -> Path:
        return self.data_dir / "document_registry.db"

    @property
    def structured_dir(self) -> Path:
        return self.data_dir / "structured"

    @property
    def structured_text_dir(self) -> Path:
        """CSV + per-table `.meta.json` for the structured store -- the committed,
        diffable source `db reindex` rebuilds `structured_dir`'s parquet and
        registry rows from (`structured/text_export.py`). Unlike
        `structured_dir`, this one is NOT in `GITIGNORE_BLOCK`: it is the
        whole point of gitignoring the parquet/DuckDB catalog in the first
        place."""
        return self.data_dir / "structured_text"

    @property
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def refine_dir(self) -> Path:
        return self.data_dir / "refine"


def note_scratch_dir(vault_dir: Path | str | None) -> Path | None:
    """Where a note write's temp file goes: the vault's `.artmind/data/` when
    `vault_dir` is a real vault. That folder is committed, but GITIGNORE_BLOCK
    ignores `*.artmind-tmp` files under it, so a crash leaves a scratch file
    that will not be committed rather than one beside the note. None (the
    note's own directory) when there is no vault or it has no `.artmind/`."""
    if vault_dir is None or not (Path(vault_dir) / ".artmind").is_dir():
        return None
    return VaultLayout(Path(vault_dir)).data_dir


# The ownership rule as a mechanism rather than prose (docs/vault.md, "What is
# in git, and what is not"). .artmind/ belongs to artmind and is versioned with
# the vault; everything below is the short list of exceptions, and each is a
# secret, a churning binary, or machine-local state.
GITIGNORE_BLOCK = """\
# ── artmind (v5) ──────────────────────────────────────────────────────────────
# .artmind/ belongs to artmind and is versioned with your vault, so a clone
# reproduces the graph without paying for extraction again. These are the
# exceptions, and each is a secret, a churning binary, or machine-local state.

# Holds the graph password. A vault is a repo you may push.
.artmind/config.env
# A SQLite binary rewritten on every ingest; merges catastrophically, and
# `artmind docs reindex` rebuilds it from vault frontmatter.
.artmind/data/document_registry.db
.artmind/data/document_registry.db-shm
.artmind/data/document_registry.db-wal
# Machine-local runtime state, meaningless on another machine.
.artmind/logs/
.artmind/state.json
.artmind/serve.json
.artmind/worker.pid
# The chat/admin UI agent's CLAUDE_CONFIG_DIR (webui/backends): auth config
# and full conversation transcripts. Private, and per machine.
.artmind/.claude-sdk-auth/
# artmind's own skills are symlinks to the installed copy; yours are not
# matched by this and stay committable.
.claude/skills/artmind-*
# Same deal for the opencode/ACP persona (custom agent modes): artmind's own
# are symlinks to the installed copy; a custom agent you author yourself
# (any other .md name under .opencode/agent/) is not matched and stays
# committable.
.opencode/agent/artmind*.md

# Locally-cached chunk vectors, split out of committed chunks.json into this
# sidecar precisely so it can be excluded. Derived from (text, model), and
# undeltable -- ten versions of one chunks.json cost 60 KB of git objects with
# embeddings inline and 20 KB with them split into embeddings.json instead. A
# clone has no embeddings.json and rebuilds it once.
.artmind/data/kg/**/embeddings.json

# Snapshots: large, opaque, and already a complete copy of what git versions.
# By extension rather than path, so one dropped anywhere stays out of both git
# and ingestion.
*.zip
*.tar.gz
*.tgz

# The structured store's parquet + DuckDB catalog are a rebuildable cache, not
# the source of truth: `.artmind/data/structured_text/` (CSV + per-table .meta.json,
# written by `db export-text`) is what's committed, and `db reindex` rebuilds
# these from it. Committing the binaries too would make every ingest a
# growing, undiffable blob in git history for no benefit over the text.
.artmind/data/structured/**/*.parquet
.artmind/data/structured/*.duckdb
.artmind/data/structured/*.duckdb.wal

# Regenerated by `vault sync` from structured_text CSV + the table mapping,
# the same way parquet is. Committing them made sync create commits on the
# receiving machine (spec 2026-09-26 R4).
.artmind/data/kg/*/table__*/
# Atomic-write scratch (R2): a staging folder's `.artmind-tmp`/`.artmind-old`
# sibling folder, and a scratch FILE -- a curation record's temp file, or a
# note rewrite's (`vault migrate-frontmatter`). A crash can leave one behind:
# git ignores it and it is harmless -- delete it. (A note rewrite's scratch
# file has a random name, so a later write does not reuse or remove it.)
.artmind/data/**/*.artmind-tmp/
.artmind/data/**/*.artmind-old/
.artmind/data/**/*.artmind-tmp
.artmind/data/**/*.artmind-old

# Not artmind's files, but a vault synced between machines needs them out:
# OS litter, and Obsidian's per-device pane layout, which changes on every
# click and so conflicts whenever two machines commit it. The rest of
# .obsidian/ (settings, plugins, their config) stays committed -- it is what
# makes the second machine behave like the first.
.DS_Store
Thumbs.db
/.obsidian/workspace.json
/.obsidian/workspace-mobile.json
# The artmind Obsidian plugin: `artmind init` installs the build matching THIS
# machine's artmind, and its settings name this machine's paths and processes,
# so each machine keeps its own.
/.obsidian/plugins/artmind/
# ── end artmind ───────────────────────────────────────────────────────────────
"""

GITATTRIBUTES_BLOCK = """\
# ── artmind (v1) ──────────────────────────────────────────────────────────────
# Generated, regenerable files: resolve whole-file, never line-merge. Without
# this, git can merge two extraction runs of one document cleanly into a
# valid-looking but mixed observations.json (spec 2026-09-26 R3).
# Do not add export-ignore, export-subst, text/eol or filter= for these paths:
# `vault sync` reads them through `git archive`, which applies those.
.artmind/data/** merge=binary
# ── end artmind ───────────────────────────────────────────────────────────────
"""

# A header is the whole line: "# ── artmind (vN) ───…", or before versioning
# (= version 1) "# ── artmind ───…" -- one or more box-drawing dashes and
# nothing after them. Anchoring the full line keeps a user's own
# "# ── artmind ─ notes" comment from being read as artmind's block. `\r`
# tolerates CRLF files: with `re.MULTILINE`, `$` matches right before the
# `\n`, so a line's trailing `\r` is consumed by `[ \t\r]*` before it, and the
# patterns below work directly on raw, un-normalised file text.
_BLOCK_START = re.compile(r"^# ── artmind(?: \(v(\d+)\))? ─+[ \t\r]*$", re.MULTILINE)
_BLOCK_END = re.compile(r"^# ── end artmind ─.*(?:\n|$)", re.MULTILINE)


def _find_blocks(text: str) -> tuple[list[tuple[int, int]], list[re.Match[str]]]:
    """`(blocks, orphans)`. `blocks` are `(start, end)` offsets of every
    recognised artmind block in `text`, in order; `end` = -1 for a start
    marker with no end marker after it (always the last block found).
    `orphans` are '# ── end artmind' lines matched by no recognised header --
    evidence of a header artmind no longer recognises (hand-edited, or from a
    version this build doesn't know), whose block is still silently active."""
    starts = list(_BLOCK_START.finditer(text))
    ends = list(_BLOCK_END.finditer(text))
    blocks: list[tuple[int, int]] = []
    claimed: set[int] = set()
    pos = 0
    for start in starts:
        if start.start() < pos:
            continue  # inside a block already claimed
        end_idx = next(
            (i for i, e in enumerate(ends) if i not in claimed and e.start() >= start.end()),
            None,
        )
        if end_idx is None:
            blocks.append((start.start(), -1))
            break
        blocks.append((start.start(), ends[end_idx].end()))
        claimed.add(end_idx)
        pos = ends[end_idx].end()
    orphans = [e for i, e in enumerate(ends) if i not in claimed]
    return blocks, orphans


def _read_raw(path: Path) -> str:
    """The file as written -- no newline translation, so CRLF survives."""
    return path.read_bytes().decode("utf-8") if path.is_file() else ""


def _line_eol(raw: str, pos: int) -> str:
    """The line ending terminating the line that starts at `pos` in `raw` --
    `"\\r\\n"` when its newline is preceded by `\\r`, else `"\\n"`."""
    nl = raw.find("\n", pos)
    if nl == -1:
        return "\n"
    return "\r\n" if nl > pos and raw[nl - 1] == "\r" else "\n"


def block_status(path: Path, block: str) -> str:
    """`"current"`, `"missing"`, `"outdated"` (an older or edited artmind
    block), `"malformed"` (a start marker with no end marker, or an end
    marker with no header artmind recognises before it) or `"duplicate"`
    (more than one artmind block) -- `vault doctor`'s check (spec R8). Line
    endings do not matter: a CRLF file holding the current block is
    current."""
    text = _read_raw(path)
    blocks, orphans = _find_blocks(text)
    if orphans:
        return "malformed"
    if not blocks:
        return "missing"
    if len(blocks) > 1:
        return "duplicate"
    start, end = blocks[0]
    if end == -1:
        return "malformed"
    return "current" if text[start:end].replace("\r\n", "\n") == block else "outdated"


def block_version(text: str) -> int | None:
    """The version in the first artmind block header found in `text` -- 1 for
    the original unversioned header -- or None when there is none. Pass a
    file's text to read what a vault holds, or a block constant to read what
    this artmind writes (`vault doctor` names both)."""
    match = _BLOCK_START.search(text)
    if match is None:
        return None
    return int(match.group(1)) if match.group(1) else 1


def _write_block(path: Path, block: str) -> bool:
    """Append `block`, or replace an existing artmind block in place, by
    splicing only the block's own bytes into the file. Lines outside the
    block -- including their exact line endings -- are the user's and are
    never touched. Returns True when the file changed."""
    raw = _read_raw(path)
    blocks, orphans = _find_blocks(raw)
    if orphans:
        first = orphans[0]
        line_no = raw.count("\n", 0, first.start()) + 1
        raise VaultError(
            f"{path}:{line_no}: found {first.group().splitlines()[0]!r} with no recognised "
            "artmind header before it, so artmind cannot tell whether that block's rules are "
            "still active -- fix the header so it reads '# ── artmind ───' (optionally "
            "'(vN)') with nothing else on the line, or delete that block by hand (from its "
            "header line through this end line), then re-run `artmind init`"
        )
    if len(blocks) > 1:
        raise VaultError(
            f"{path}: has {len(blocks)} artmind blocks, so artmind cannot tell which one is "
            "its own -- delete all but one (from its '# ── artmind' line through its "
            "'# ── end artmind' line), then re-run `artmind init`"
        )
    if not blocks:
        eol = _line_eol(raw, 0)
        block_eol = block if eol == "\n" else block.replace("\n", eol)
        if not raw:
            new = block_eol
        else:
            prefix = raw if raw.endswith("\n") else raw + eol
            new = prefix + eol + block_eol
    else:
        start, end = blocks[0]
        if end == -1:
            raise VaultError(
                f"{path}: the artmind block has no '# ── end artmind' line, so artmind "
                "cannot tell where its rules stop and yours begin -- fix it by hand, "
                "then re-run `artmind init`"
            )
        if raw[start:end].replace("\r\n", "\n") == block:
            return False
        eol = _line_eol(raw, start)
        block_eol = block if eol == "\n" else block.replace("\n", eol)
        new = raw[:start] + block_eol + raw[end:]
    if new == raw:
        return False
    path.write_bytes(new.encode("utf-8"))
    return True


def write_gitignore(root: Path) -> bool:
    """Write artmind's ignore rules into the vault's `.gitignore` (R4).

    Appends to an established repo's own rules, and replaces an older
    artmind block in place -- so re-running `init` upgrades an existing
    vault. Newly ignored paths that are already tracked stay tracked until
    the user runs the `git rm --cached` that `vault doctor` prints (D1:
    artmind never does). Returns True when the file changed."""
    return _write_block(Path(root) / ".gitignore", GITIGNORE_BLOCK)


def write_gitattributes(root: Path) -> bool:
    """Write artmind's merge rules into the vault's `.gitattributes` (R3),
    same append/replace-in-place contract as `write_gitignore`."""
    return _write_block(Path(root) / ".gitattributes", GITATTRIBUTES_BLOCK)


def read_state(layout: VaultLayout) -> dict:
    """The machine-local cursor file (`state.json`), or `{}` if absent.

    Several independent cursors share this one file -- `last_ingested_commit`
    (documented, not yet implemented), `last_structured_commit` (`vault
    sync`'s structured-store bookmark; the graph's lives in the graph) and,
    until a vault's first sync after the bookmark split, the retired
    `last_synced_commit` -- see `VaultLayout.state_json`'s docstring. Corrupt
    or unreadable JSON is treated as absent rather than raising: a
    hand-edited or partially-written state.json should not brick every
    command that touches it.
    """
    path = layout.state_json
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def write_state(layout: VaultLayout, updates: dict, *, remove: tuple[str, ...] = ()) -> None:
    """Merge `updates` into `state.json`, preserving every other key already
    there, and drop the keys named in `remove`. Multiple cursors coexist in
    this one file (see `read_state`) -- a naive whole-file overwrite would
    silently erase whichever this call doesn't mention.
    """
    path = layout.state_json
    state = read_state(layout)
    state.update(updates)
    for key in remove:
        state.pop(key, None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
