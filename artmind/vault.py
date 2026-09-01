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

import os
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
    def derived_dir(self) -> Path:
        """Markdown converted from binaries. Visible and committed, NOT hidden:
        these are editable documents that become vault-native on promotion."""
        return self.root / "_derived"

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

    # ── machine-local, gitignored ────────────────────────────────────────────
    @property
    def config_env(self) -> Path:
        return self.artmind_dir / "config.env"

    @property
    def state_json(self) -> Path:
        """The ingest cursor: `last_ingested_commit`."""
        return self.artmind_dir / "state.json"

    @property
    def logs_dir(self) -> Path:
        return self.artmind_dir / "logs"

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
    def snapshots_dir(self) -> Path:
        return self.data_dir / "snapshots"

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def refine_dir(self) -> Path:
        return self.data_dir / "refine"


# The ownership rule as a mechanism rather than prose (docs/vault.md, "What is
# in git, and what is not"). .artmind/ belongs to artmind and is versioned with
# the vault; everything below is the short list of exceptions, and each is a
# secret, a churning binary, or machine-local state.
GITIGNORE_BLOCK = """\
# ── artmind ───────────────────────────────────────────────────────────────────
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
# artmind's own skills are symlinks to the installed copy; yours are not
# matched by this and stay committable.
.claude/skills/artmind-*

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
# ── end artmind ───────────────────────────────────────────────────────────────
"""

_GITIGNORE_SENTINEL = "# ── artmind ─"


def write_gitignore(root: Path) -> bool:
    """Add artmind's ignore rules to the vault's `.gitignore`.

    Appends rather than replaces — the vault may be an established repo with
    rules of its own — and is idempotent, so re-running `init` is safe.
    Returns True when the file was changed.
    """
    target = Path(root) / ".gitignore"
    existing = target.read_text(encoding="utf-8") if target.is_file() else ""
    if _GITIGNORE_SENTINEL in existing:
        return False
    prefix = existing if existing.endswith("\n") or not existing else existing + "\n"
    target.write_text(prefix + ("\n" if prefix else "") + GITIGNORE_BLOCK, encoding="utf-8")
    return True
