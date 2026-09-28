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

# git subcommands that write history, the index, refs (local or on a remote),
# the object store, or the tree. `update-ref`/`symbolic-ref` included: spec
# 2026-09-26 D2 allows no write at all, not even a private ref (the dropped
# Plan B `refs/artmind/` pin).
WRITE_SUBCOMMANDS = (
    "add", "commit", "rm", "push", "pull", "merge", "rebase", "reset", "checkout", "stash",
    "update-ref", "symbolic-ref", "gc", "prune",
)
_WRITES = "|".join(re.escape(s) for s in WRITE_SUBCOMMANDS)
PATTERNS = [
    re.compile(r"""["']git\s+(%s)(?![\w-])""" % _WRITES),                  # "git commit ..."
    re.compile(r"""["']git["']\s*,\s*["'](%s)["']""" % _WRITES),           # ["git", "commit", ...]
    # vault_sync's own wrapper, whose argv has no "git" in it:
    # _git(vault_dir, ["update-ref", ...])
    re.compile(r"""\b_git\(\s*[^,()]+,\s*\[\s*["'](%s)["']""" % _WRITES),
    re.compile(r"\b(commit_paths|remove_paths|maybe_push)\b"),
]


@pytest.mark.parametrize("line", [
    'run_command(["git", "update-ref", "refs/artmind/last-synced/graph", sha], cwd=vault_dir)',
    '_git(vault_dir, ["update-ref", f"{PREFIX}/{store}", commit])',
    '_git(vault_dir, ["commit", "-m", "x"])',
    'subprocess.run("git update-ref refs/artmind/x HEAD", shell=True)',
    'run_command(["git", "symbolic-ref", "HEAD", "refs/heads/x"], cwd=d)',
    'run_command(["git", "gc", "--prune=now"], cwd=d)',
])
def test_the_patterns_catch_a_write(line):
    """The guard must actually fire -- a write subcommand missing from the
    list (as `update-ref` once was) makes every scan above vacuous for it."""
    assert any(p.search(line) for p in PATTERNS), line


@pytest.mark.parametrize("line", [
    '_git(vault_dir, ["rev-parse", "--absolute-git-dir"])',
    '_git(vault_dir, ["diff", "--no-renames", "--name-status", "-z", base, head])',
    'run_command(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=vault_dir)',
    'run_command(["git", "merge-base", "--is-ancestor", commit, head], cwd=vault_dir)',
    'subprocess.run("git merge-base --is-ancestor a b", shell=True)',
])
def test_the_patterns_leave_reads_alone(line):
    assert not any(p.search(line) for p in PATTERNS), line


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
