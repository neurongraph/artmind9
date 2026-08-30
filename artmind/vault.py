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
