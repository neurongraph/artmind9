"""Guard for spec 2026-09-26 D1: artmind never writes to the vault's git repo
-- with one narrow, named exception (`ALLOWED`).

Obsidian Git owns commit/pull/push. A source scan is deliberately blunt: any
reintroduced write call fails here even if no behavioural test exercises the
code path that makes it (the worker's per-file commit was exactly such a path).

It does not follow writes through a wrapper function that assembles the git
command internally, or across a multi-line list literal -- it's a floor under
code review, not a replacement for it. What it does catch, on one line:
`"git ..."` strings and `["git", ...]` argvs (global options like `-c k=v` in
front of the subcommand included), the `_git(...)`/`_read(...)` wrappers'
`[..]` argvs, an argv whose subcommand is a splat (`["git", *args]`, only the
named wrapper definitions in `SPLAT_WRAPPERS` are allowed), and a `["git", `
argv that opens a list literal without closing it (it cannot be scanned).
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
    "restore", "switch", "clean", "cherry-pick", "revert", "mv", "update-index", "read-tree",
    "apply", "am", "commit-tree", "write-tree",
)
_WRITES = "|".join(re.escape(s) for s in WRITE_SUBCOMMANDS)
# Options git accepts before the subcommand: `-c k=v`, `-C dir`, `--no-pager`, `--git-dir=x`.
_OPTS_LIST = r"""(?:["']-[cC]["']\s*,\s*[^,\]]+,\s*|["']--?[\w=.\-/]+["']\s*,\s*)*"""
_OPTS_STR = r"""(?:-[cC]\s+\S+\s+|--?[\w=.-]+\s+)*"""
PATTERNS = [
    re.compile(r"""["']git\s+%s(%s)(?![\w-])""" % (_OPTS_STR, _WRITES)),                  # "git -c a=b commit ..."
    re.compile(r"""["']git["']\s*,\s*%s["'](%s)["']""" % (_OPTS_LIST, _WRITES)),          # ["git", "-c", "a=b", "commit", ...]
    # the wrappers whose argv has no "git" in it:
    # _git(vault_dir, ["update-ref", ...]) / _read(vault_dir, ["commit", ...])
    re.compile(r"""\b_(?:git|read)\(\s*[^,()]+,\s*\[\s*%s["'](%s)["']""" % (_OPTS_LIST, _WRITES)),
    re.compile(r"\b(commit_paths|remove_paths|maybe_push)\b"),
]
# An argv whose subcommand is not a literal cannot be judged: `["git", *args]`,
# `["git"] + args`. Only the wrappers named below may build one.
SPLAT = [
    re.compile(r"""\[\s*["']git["']\s*,\s*(\*)"""),
    re.compile(r"""\[\s*["']git["']\s*(\])\s*\+"""),
]
# Each of these files defines ONE such wrapper (its callers are scanned as
# `_git(...)`/`_read(...)` above): a read-only one by review, or, kg_pull's,
# a throwaway clone of someone else's repo, never the vault.
SPLAT_WRAPPERS: dict[str, int] = {
    "artmind/vault_sync.py": 1,
    "artmind/vault_doctor.py": 1,
    "artmind/vault_resolve.py": 1,
    "artmind/kg_pull.py": 1,
}
_GIT_ARGV_OPEN = re.compile(r"""\[\s*["']git["']\s*,?""")


@pytest.mark.parametrize("line", [
    'run_command(["git", "update-ref", "refs/artmind/last-synced/graph", sha], cwd=vault_dir)',
    '_git(vault_dir, ["update-ref", f"{PREFIX}/{store}", commit])',
    '_git(vault_dir, ["commit", "-m", "x"])',
    '_read(vault_dir, ["commit", "-m", "x"])',
    '_read(self._vault_dir, ["reset", "--hard", rev])',
    '_git(vault_dir, ["-c", "user.name=x", "commit", "-m", "x"])',
    'subprocess.run("git update-ref refs/artmind/x HEAD", shell=True)',
    'subprocess.run("git -c user.name=x commit -m x", shell=True)',
    'subprocess.run("git --no-pager rm -r notes", shell=True)',
    'run_command(["git", "symbolic-ref", "HEAD", "refs/heads/x"], cwd=d)',
    'run_command(["git", "gc", "--prune=now"], cwd=d)',
    'run_command(["git", "-c", "user.name=x", "commit", "-m", "x"], cwd=d)',
    'run_command(["git", "-C", other, "push"], cwd=d)',
    'run_command(["git", "--no-pager", "restore", "--", path], cwd=d)',
    'run_command(["git", "update-index", "--force-remove", "--", p], cwd=d)',
    'run_command(["git", "clean", "-fd"], cwd=d)',
])
def test_the_patterns_catch_a_write(line):
    """The guard must actually fire -- a write subcommand missing from the
    list (as `update-ref` once was) makes every scan above vacuous for it."""
    assert any(p.search(line) for p in PATTERNS), line


@pytest.mark.parametrize("line", [
    '_git(vault_dir, ["rev-parse", "--absolute-git-dir"])',
    '_read(vault_dir, ["rev-parse", "HEAD"])',
    '_read(vault_dir, ["ls-tree", "-r", "-z", rev, "--", unit])',
    '_git(vault_dir, ["diff", "--no-renames", "--name-status", "-z", base, head])',
    'run_command(["git", "cat-file", "-e", f"{commit}^{{commit}}"], cwd=vault_dir)',
    'run_command(["git", "merge-base", "--is-ancestor", commit, head], cwd=vault_dir)',
    'run_command(["git", "-c", "core.quotepath=off", "diff", "--name-only"], cwd=d)',
    'run_command(["git", "log", "--grep", "add"], cwd=d)',
    'subprocess.run("git merge-base --is-ancestor a b", shell=True)',
])
def test_the_patterns_leave_reads_alone(line):
    assert not any(p.search(line) for p in PATTERNS), line


@pytest.mark.parametrize("line", [
    'run_command(["git", *args], cwd=vault_dir)',
    'subprocess.run(["git", *argv])',
    'subprocess.run(["git"] + args, cwd=cwd)',
])
def test_a_splat_argv_is_flagged(line):
    assert any(p.search(line) for p in SPLAT), line
    assert _hits("artmind/some_other.py", line)


# The exceptions: source file -> each write subcommand it may use, with the
# ONE line shape allowed for it. Any other write subcommand in that file --
# these same ones in another shape (`checkout -f`, `add -A`, `add .`, a
# checkout without `--`) -- and these same ones anywhere else still fail.
#
# `vault resolve` (spec 2026-09-26 R7; D1/D2 as amended): settling a merge
# conflict in artmind's generated files means staging the side it picks --
# `git checkout <HEAD|MERGE_HEAD> -- <paths>` and `git add -- <paths>` --
# where <paths> is an explicit list of files (`*batch`), only under
# `.artmind/data/**` (`vault_resolve._assert_scoped`), only while a merge is
# in progress, and never a commit. Files the picked side lacks are deleted
# from the working tree and staged with the same `git add -- <paths>`.
ALLOWED: dict[str, dict[str, re.Pattern]] = {
    "artmind/vault_resolve.py": {
        "checkout": re.compile(r"""\["git", "checkout", rev, "--", \*batch\]"""),
        "add": re.compile(r"""\["git", "add", "--", \*batch\]"""),
    },
}


def _covered(shape: re.Pattern, line: str, start: int) -> bool:
    return any(m.start() <= start < m.end() for m in shape.finditer(line))


def _hits(rel: str, text: str) -> list[str]:
    allowed = ALLOWED.get(rel, {})
    splat_budget = SPLAT_WRAPPERS.get(rel, 0)
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        where = f"{rel}:{n}: {line.strip()}"
        for p in PATTERNS:
            for m in p.finditer(line):
                shape = allowed.get(m.group(1))
                if shape is None or not _covered(shape, line, m.start()):
                    hits.append(where)
        for p in SPLAT:
            for _ in p.finditer(line):
                if splat_budget <= 0:
                    hits.append(where)
                splat_budget -= 1
        if _GIT_ARGV_OPEN.search(line) and line.count("[") > line.count("]"):
            hits.append(where + "   <- a git argv list literal spans lines; this scan cannot read it")
    return hits


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(REPO)))
def test_no_git_write_calls(path):
    rel = str(path.relative_to(REPO))
    hits = _hits(rel, path.read_text(encoding="utf-8"))
    assert not hits, "artmind must not write to the vault's git repo:\n" + "\n".join(hits)


RESOLVE = "artmind/vault_resolve.py"
CHECKOUT = 'run_command(["git", "checkout", rev, "--", *batch], cwd=vault_dir, extra_env=_LITERAL)'
ADD = 'run_command(["git", "add", "--", *batch], cwd=vault_dir, extra_env=_LITERAL)'


def test_the_resolve_exception_is_exactly_checkout_and_add_by_explicit_path():
    assert set(ALLOWED) == {RESOLVE}
    assert set(ALLOWED[RESOLVE]) == {"checkout", "add"}
    assert _hits(RESOLVE, CHECKOUT) == []
    assert _hits(RESOLVE, ADD) == []
    assert _hits("artmind/vault_sync.py", CHECKOUT), "the same call anywhere else still fails"
    assert _hits("artmind/vault_sync.py", ADD)


@pytest.mark.parametrize("line", [
    'run_command(["git", "commit", "-m", "x"], cwd=d)',
    'run_command(["git", "rm", "--cached", p], cwd=d)',
    'run_command(["git", "-c", "user.name=x", "commit", "-m", "x"], cwd=d)',
    '_read(vault_dir, ["commit", "-m", "x"])',
    'run_command(["git", "checkout", "-f", rev, "--", *batch], cwd=d)',
    'run_command(["git", "checkout", "-f"], cwd=d)',
    'run_command(["git", "checkout", rev, *batch], cwd=d)',
    'run_command(["git", "checkout", rev, "--", "."], cwd=d)',
    'run_command(["git", "checkout", rev, "--", ".artmind"], cwd=d)',
    'run_command(["git", "checkout", "--", *batch], cwd=d)',
    'run_command(["git", "add", "-A"], cwd=d)',
    'run_command(["git", "add", "."], cwd=d)',
    'run_command(["git", "add", "-A", "--", *batch], cwd=d)',
    'run_command(["git", "add", "--", "."], cwd=d)',
    'run_command(["git", "add", "--", ".artmind/data"], cwd=d)',
    'run_command(["git", "add", *batch], cwd=d)',
    'run_command(["git", "update-index", "--force-remove", "--", *batch], cwd=d)',
    'run_command(["git", "restore", "--source", rev, "--", *batch], cwd=d)',
    'run_command(["git",\n "checkout"], cwd=d)',
])
def test_the_resolve_exception_flags_every_other_shape(line):
    assert _hits(RESOLVE, line), line


@pytest.mark.parametrize("line", [
    'run_command(["git", *args], cwd=d)',
    'run_command(["git"] + args, cwd=d)',
])
def test_a_splat_wrapper_is_allowed_exactly_as_often_as_counted(line):
    """vault_resolve.py owns one splat wrapper: the first is let through, a
    second (a new unreviewed argv builder) is not."""
    assert _hits(RESOLVE, line) == []
    assert _hits(RESOLVE, line + "\n" + line)
    assert _hits("artmind/vault_sync.py", "\n".join([line] * 2))
    assert _hits("artmind/new_module.py", line)


def test_the_scan_flags_an_offending_source():
    source = (
        "def f(vault_dir):\n"
        "    run_command(['git', '-c', 'user.name=x', 'commit', '-m', 'x'], cwd=vault_dir)\n"
        "    _read(vault_dir, ['reset', '--hard'])\n"
        "    run_command(['git', 'add', '-A'], cwd=vault_dir)\n"
        "    run_command([\n"
        "        'git', 'checkout',\n"
        "    ], cwd=vault_dir)\n"
    )
    assert len(_hits(RESOLVE, source)) >= 3
    assert _hits(RESOLVE, "def f(v):\n    " + CHECKOUT + "\n    " + ADD + "\n") == []


def test_every_allowance_is_still_used():
    """An exception nothing needs any more is removed, not left open."""
    for rel, shapes in ALLOWED.items():
        text = (REPO / rel).read_text(encoding="utf-8")
        for sub, shape in shapes.items():
            assert shape.search(text), f"{rel} no longer runs git {sub} in the allowed shape; drop it from ALLOWED"


def test_every_splat_wrapper_is_still_there():
    for rel, count in SPLAT_WRAPPERS.items():
        text = (REPO / rel).read_text(encoding="utf-8")
        found = sum(len(list(p.finditer(line))) for line in text.splitlines() for p in SPLAT)
        assert found == count, f"{rel} has {found} splat git argvs, SPLAT_WRAPPERS says {count}"
