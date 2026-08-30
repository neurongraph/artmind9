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
