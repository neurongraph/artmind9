"""Guard for spec 2026-09-26 D1: artmind never writes to the vault's git repo.

Obsidian Git owns commit/pull/push. A source scan is deliberately blunt: any
reintroduced write call fails here even if no behavioural test exercises the
code path that makes it (the worker's per-file commit was exactly such a path).

It does not follow writes through a wrapper function that assembles the git
command internally, or across a multi-line list literal -- it's a floor under
code review, not a replacement for it.
"""
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SOURCES = sorted(
    p for root in ("artmind", "utils") for p in (REPO / root).rglob("*.py")
)

# git subcommands that write history, the index, refs on a remote, or the tree
WRITE_SUBCOMMANDS = ("add", "commit", "rm", "push", "pull", "merge", "rebase", "reset", "checkout", "stash")
PATTERNS = [
    re.compile(r"""["']git\s+(%s)\b""" % "|".join(WRITE_SUBCOMMANDS)),          # "git commit ..."
    re.compile(r"""["']git["']\s*,\s*["'](%s)["']""" % "|".join(WRITE_SUBCOMMANDS)),  # ["git", "commit", ...]
    re.compile(r"\b(commit_paths|remove_paths|maybe_push)\b"),
]

# Writes against a repo that is NOT the user's vault, or (phase 5) `vault
# resolve`'s scoped staging. Each entry needs a one-line reason. Empty today.
ALLOWED: set[str] = set()


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_git_write_calls(path):
    rel = str(path.relative_to(REPO))
    if rel in ALLOWED:
        pytest.skip("writes to a non-vault repo; see ALLOWED")
    hits = [
        f"{rel}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(p.search(line) for p in PATTERNS)
    ]
    assert not hits, "artmind must not write to the vault's git repo:\n" + "\n".join(hits)
