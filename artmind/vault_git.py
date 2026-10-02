"""Read-only git helpers for the vault (spec 2026-09-26, D1/D2).

artmind never writes to the vault's git repo -- the Obsidian Git plugin owns
commit, pull and push (the one exception, `vault new`'s bootstrap commit and
first push at creation, lives in vault_new.py, not here). What remains here only reads: provenance (`HEAD`) and
dirtiness for snapshot manifests.
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


