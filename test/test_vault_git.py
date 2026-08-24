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
