"""`vault sync`: git-diff-driven, CDC-style replay (spec
docs/superpowers/specs/2026-09-25-vault-sync-design.md)."""
import subprocess

import pytest

import artmind.vault_sync as vs


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _commit_all(path, message):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=path, check=True)


@pytest.fixture()
def repo(tmp_path):
    _init_git_repo(tmp_path)
    return tmp_path


def test_head_sha_returns_the_current_commit(repo):
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")

    sha = vs.head_sha(repo)

    log = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    assert sha == log


def test_head_sha_raises_a_clear_error_when_the_repo_has_no_commits_yet(repo):
    """Exactly the state `artmind init` leaves a fresh vault in (`git init`,
    never a commit) -- must raise a specific, actionable VaultSyncError, not
    a generic git-command-failed message."""
    with pytest.raises(vs.VaultSyncError, match="no commits yet"):
        vs.head_sha(repo)


def test_diff_name_status_reports_added_files(repo):
    scope = repo / "sub"
    scope.mkdir()
    (scope / "f.txt").write_text("x")
    _commit_all(repo, "first")

    rows = vs._diff_name_status(repo, vs.EMPTY_TREE_SHA, vs.head_sha(repo), scope)

    assert rows == [("A", "sub/f.txt")]


def test_diff_name_status_reports_removed_files(repo):
    scope = repo / "sub"
    scope.mkdir()
    (scope / "f.txt").write_text("x")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)
    (scope / "f.txt").unlink()
    _commit_all(repo, "second")

    rows = vs._diff_name_status(repo, base, vs.head_sha(repo), scope)

    assert rows == [("D", "sub/f.txt")]


def test_diff_name_status_reports_modified_files(repo):
    scope = repo / "sub"
    scope.mkdir()
    (scope / "f.txt").write_text("x")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)
    (scope / "f.txt").write_text("y")
    _commit_all(repo, "second")

    rows = vs._diff_name_status(repo, base, vs.head_sha(repo), scope)

    assert rows == [("M", "sub/f.txt")]


def test_show_returns_none_for_a_path_absent_at_that_revision(repo):
    (repo / "a.txt").write_text("x")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)

    assert vs._show(repo, base, "never_existed.txt") is None


def test_show_returns_the_content_at_that_revision(repo):
    (repo / "a.txt").write_text("hello\n")
    _commit_all(repo, "first")
    base = vs.head_sha(repo)
    (repo / "a.txt").write_text("changed\n")
    _commit_all(repo, "second")

    assert vs._show(repo, base, "a.txt") == "hello\n"
