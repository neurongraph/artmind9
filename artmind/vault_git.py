"""Read-only git helpers for the vault (spec 2026-09-26, D1/D2).

artmind never writes to the vault's git repo -- the Obsidian Git plugin owns
commit, pull and push. What remains here only reads: provenance (`HEAD`),
dirtiness for snapshot manifests, and configuring a remote at `init` time.
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from paths import ARTMIND_VAULT_DIR
from utils.functions import run_command


def _vault_root() -> Path | None:
    if ARTMIND_VAULT_DIR is None or not ARTMIND_VAULT_DIR.is_dir():
        return None
    if not (ARTMIND_VAULT_DIR / ".git").exists():
        logger.debug("vault_git: {} is not a git repo", ARTMIND_VAULT_DIR)
        return None
    return ARTMIND_VAULT_DIR


def current_commit() -> str | None:
    """The vault's git HEAD sha at this moment — provenance (`_source_commit`),
    never identity. None when there's no vault, no git repo, or no commits yet."""
    vault = _vault_root()
    if vault is None:
        return None
    # 128 is "no commits yet", which is exactly the state `artmind init` leaves
    # a new vault in -- expected, not a failure worth an ERROR line.
    rc, out, _ = run_command(["git", "rev-parse", "HEAD"], cwd=vault, expected_codes=(128,))
    return out.strip() if rc == 0 else None


def is_dirty() -> bool | None:
    """Whether the vault has uncommitted changes right now. `None` (not
    `False`) when there's no vault, no git repo, or the check itself fails --
    a snapshot manifest recording `vault_dirty: false` for a vault that was
    never checked would read as a real guarantee it isn't."""
    vault = _vault_root()
    if vault is None:
        return None
    rc, out, _ = run_command(["git", "status", "--porcelain"], cwd=vault)
    if rc != 0:
        return None
    return bool(out.strip())


def add_remote(root: Path, url: str, name: str = "origin") -> str:
    """Configure a git remote for `root` (`artmind init --interactive`/`--remote`).
    Configuration, not transport: artmind still never pushes to it.

    Takes an explicit `root` rather than going through `_vault_root()`, since
    this runs at scaffold time -- before `root` is necessarily the process's
    resolved "active" vault.

    Returns "added", "exists" (a `name` remote is already configured -- left
    alone rather than silently repointed), or "failed" (not a git repo yet, or
    the git command itself errored). Never raises.
    """
    root = Path(root)
    if not (root / ".git").exists():
        return "failed"
    rc, out, _ = run_command(["git", "remote"], cwd=root)
    if rc == 0 and name in out.split():
        return "exists"
    rc, out, err = run_command(["git", "remote", "add", name, url], cwd=root)
    if rc != 0:
        logger.warning("vault_git: git remote add failed ({}): {}", rc, err or out)
        return "failed"
    logger.info("vault_git: added remote {} -> {}", name, url)
    return "added"
