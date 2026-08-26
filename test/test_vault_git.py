"""Tests for artmind.vault_git: commit-per-frontmatter-change, opt-in push."""

import subprocess

import pytest

import artmind.vault_git as vault_git


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    _init_git_repo(v)
    monkeypatch.setattr(vault_git, "ARTMIND_VAULT_DIR", v)
    return v


def test_commit_paths_commits_a_new_file(vault):
    f = vault / "doc.md"
    f.write_text("hello\n")
    assert vault_git.commit_paths([f], "seed doc.md") is True

    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    ).stdout
    assert "seed doc.md" in log


def test_commit_paths_is_a_noop_when_nothing_changed(vault):
    f = vault / "doc.md"
    f.write_text("hello\n")
    assert vault_git.commit_paths([f], "first") is True
    # Same bytes, nothing staged the second time -- the emergent "no-op" case.
    assert vault_git.commit_paths([f], "second") is False


def test_commit_paths_returns_false_when_vault_is_not_a_git_repo(tmp_path, monkeypatch):
    v = tmp_path / "not_a_repo"
    v.mkdir()
    monkeypatch.setattr(vault_git, "ARTMIND_VAULT_DIR", v)
    f = v / "doc.md"
    f.write_text("hello\n")
    assert vault_git.commit_paths([f], "msg") is False


def test_commit_paths_returns_false_when_no_vault_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(vault_git, "ARTMIND_VAULT_DIR", None)
    f = tmp_path / "doc.md"
    f.write_text("hello\n")
    assert vault_git.commit_paths([f], "msg") is False


def test_commit_paths_returns_false_for_empty_path_list(vault):
    assert vault_git.commit_paths([], "msg") is False


def test_maybe_push_is_a_noop_unless_opted_in(vault, monkeypatch):
    monkeypatch.setattr(vault_git, "load_env", lambda: {})
    calls = []
    monkeypatch.setattr(vault_git, "run_command", lambda *a, **k: calls.append(a) or (0, "", ""))
    vault_git.maybe_push()
    assert calls == []


def test_maybe_push_runs_git_push_when_opted_in(vault, monkeypatch):
    monkeypatch.setattr(vault_git, "load_env", lambda: {"ARTMIND_VAULT_GIT_PUSH": "1"})
    calls = []

    def fake_run(cmd, cwd=None, **k):
        calls.append(cmd)
        return (0, "", "")

    monkeypatch.setattr(vault_git, "run_command", fake_run)
    vault_git.maybe_push()
    assert any("git push" in c for c in calls)


def test_maybe_push_failure_is_swallowed_not_raised(vault, monkeypatch):
    monkeypatch.setattr(vault_git, "load_env", lambda: {"ARTMIND_VAULT_GIT_PUSH": "1"})
    monkeypatch.setattr(vault_git, "run_command", lambda *a, **k: (1, "", "no remote"))
    vault_git.maybe_push()  # must not raise


# ── move_path (Phase 5: derived-markdown promotion) ─────────────────────────


def test_move_path_moves_a_tracked_file(vault):
    old = vault / "_derived" / "banking" / "deck.md"
    old.parent.mkdir(parents=True)
    old.write_text("hello\n")
    vault_git.commit_paths([old], "seed")

    new = vault / "banking" / "deck.md"
    assert vault_git.move_path(old, new) is True
    assert not old.exists()
    assert new.read_text() == "hello\n"


def test_move_path_creates_destination_parent_dirs(vault):
    old = vault / "a.md"
    old.write_text("x\n")
    vault_git.commit_paths([old], "seed")

    new = vault / "brand" / "new" / "dir" / "a.md"
    assert vault_git.move_path(old, new) is True
    assert new.exists()


def test_move_path_returns_false_when_no_vault_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(vault_git, "ARTMIND_VAULT_DIR", None)
    old = tmp_path / "a.md"
    old.write_text("x\n")
    assert vault_git.move_path(old, tmp_path / "b.md") is False


def test_move_path_returns_false_when_source_untracked(vault):
    # Never committed -- git mv has nothing to move.
    old = vault / "untracked.md"
    old.write_text("x\n")
    assert vault_git.move_path(old, vault / "elsewhere.md") is False


# ── remove_paths (Phase 5: docs archive) ────────────────────────────────────


def test_remove_paths_removes_and_commits(vault):
    f = vault / "doc.md"
    f.write_text("hello\n")
    vault_git.commit_paths([f], "seed")

    assert vault_git.remove_paths([f], "archive doc.md") is True
    assert not f.exists()
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    ).stdout
    assert "archive doc.md" in log


def test_remove_paths_returns_false_when_no_vault_configured(monkeypatch, tmp_path):
    monkeypatch.setattr(vault_git, "ARTMIND_VAULT_DIR", None)
    f = tmp_path / "doc.md"
    f.write_text("hello\n")
    assert vault_git.remove_paths([f], "msg") is False


def test_remove_paths_returns_false_for_empty_path_list(vault):
    assert vault_git.remove_paths([], "msg") is False


def test_remove_paths_returns_false_when_untracked(vault):
    f = vault / "untracked.md"
    f.write_text("x\n")
    assert vault_git.remove_paths([f], "msg") is False
    assert f.exists()  # nothing removed on failure


# ── is_dirty ─────────────────────────────────────────────────────────────────


def test_is_dirty_false_on_a_clean_vault(vault):
    f = vault / "doc.md"
    f.write_text("hello\n")
    vault_git.commit_paths([f], "seed")
    assert vault_git.is_dirty() is False


def test_is_dirty_true_with_uncommitted_changes(vault):
    f = vault / "doc.md"
    f.write_text("hello\n")
    vault_git.commit_paths([f], "seed")
    f.write_text("edited\n")
    assert vault_git.is_dirty() is True


def test_is_dirty_none_when_no_vault_configured(monkeypatch):
    monkeypatch.setattr(vault_git, "ARTMIND_VAULT_DIR", None)
    assert vault_git.is_dirty() is None


def test_is_dirty_none_when_not_a_git_repo(tmp_path, monkeypatch):
    v = tmp_path / "not_a_repo"
    v.mkdir()
    monkeypatch.setattr(vault_git, "ARTMIND_VAULT_DIR", v)
    assert vault_git.is_dirty() is None
