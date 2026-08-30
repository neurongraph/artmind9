"""Vault discovery and layout (docs/vault.md).

A **vault** is a directory containing `.artmind/`. It is the user's Obsidian
vault, their git repo and their artmind knowledge base at once. You do not
select a vault — you are standing in one, or you are not, exactly as with a git
repo.

This module is deliberately pure and free of `paths` imports: `paths` imports
*this*, runs at import time for every command, and must stay cheap. Keeping
discovery here also makes it unit-testable without reimporting `paths`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# The marker directory. A vault is any directory containing one.
MARKER = ".artmind"


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
        if (candidate / MARKER).is_dir():
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
        if not (root / MARKER).is_dir():
            raise VaultError(
                f"{source}={value!r} is not an artmind vault ({root / MARKER} not found). "
                f"Run `artmind init` inside it to make it one."
            )
        return root
    return find_vault(Path.cwd())


@dataclass(frozen=True)
class VaultLayout:
    """Where everything sits inside a vault (docs/vault.md, "Layout").

    One place that knows the layout, so a path is never spelled out twice. The
    split that matters: everything under ``data_dir`` is derived and gitignored;
    everything else under ``artmind_dir`` is authoritative and committed.
    """

    root: Path

    # ── authoritative, committed ──────────────────────────────────────────────
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

    # ── derived, gitignored ──────────────────────────────────────────────────
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
