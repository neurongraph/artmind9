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

# git subcommands that write history, the index, refs, refs on a remote, or the tree
WRITE_SUBCOMMANDS = (
    "add", "commit", "rm", "push", "pull", "merge", "rebase", "reset", "checkout", "stash", "update-ref",
)
PATTERNS = [
    re.compile(r"""["']git\s+(%s)\b""" % "|".join(WRITE_SUBCOMMANDS)),          # "git commit ..."
    re.compile(r"""["']git["']\s*,\s*["'](%s)["']""" % "|".join(WRITE_SUBCOMMANDS)),  # ["git", "commit", ...]
    re.compile(r"\b(commit_paths|remove_paths|maybe_push)\b"),
    # a wrapper's argv without "git" in it: _git(vault_dir, ["update-ref", ...])
    re.compile(r"""\[\s*["']update-ref["']"""),
]

# Writes against a repo that is NOT the user's vault, or (phase 5) `vault
# resolve`'s scoped staging. Each entry needs a one-line reason. Empty today.
ALLOWED: set[str] = set()

# Single lines that may write, keyed by file, each with its reason. Matched
# on the stripped line text, so any edit to the call re-opens the question.
ALLOWED_LINES: dict[str, dict[str, str]] = {
    "artmind/vault_sync.py": {
        '_git(vault_dir, ["update-ref", f"{PIN_REF_PREFIX}/{store}", commit])': (
            "spec 2026-09-26 D2/§6 A2: pins a store's bookmark under the private "
            "refs/artmind/ namespace against gc -- no index, no working tree, never "
            "sent by a branch push"
        ),
    },
}


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_git_write_calls(path):
    rel = str(path.relative_to(REPO))
    if rel in ALLOWED:
        pytest.skip("writes to a non-vault repo; see ALLOWED")
    allowed = ALLOWED_LINES.get(rel, {})
    hits = [
        f"{rel}:{n}: {line.strip()}"
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if any(p.search(line) for p in PATTERNS) and line.strip() not in allowed
    ]
    assert not hits, "artmind must not write to the vault's git repo:\n" + "\n".join(hits)


def test_each_allowed_line_exists_exactly_once():
    """An allowance for a line that is gone (or duplicated) is a stale hole."""
    for rel, lines in ALLOWED_LINES.items():
        text = [line.strip() for line in (REPO / rel).read_text(encoding="utf-8").splitlines()]
        for line in lines:
            assert text.count(line) == 1, f"{rel}: expected exactly one {line!r}"


def test_the_pinned_refs_are_private_to_artmind():
    """Never refs/heads or refs/tags: those are what a branch or tag push sends."""
    from artmind.vault_sync import PIN_REF_PREFIX

    assert PIN_REF_PREFIX.startswith("refs/artmind/")
