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


# ── add_remote (artmind init --interactive / --remote) ──────────────────────
# Takes an explicit root rather than going through ARTMIND_VAULT_DIR, since it
# runs at scaffold time before the new vault is necessarily "active" -- so no
# monkeypatching of vault_git module globals needed here, unlike above.


def test_add_remote_configures_origin_on_a_fresh_repo(tmp_path):
    _init_git_repo(tmp_path)

    status = vault_git.add_remote(tmp_path, "https://github.com/example/vault.git")

    assert status == "added"
    remotes = subprocess.run(
        ["git", "remote", "-v"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "https://github.com/example/vault.git" in remotes


def test_add_remote_leaves_an_existing_origin_alone(tmp_path):
    _init_git_repo(tmp_path)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/example/first.git"],
        cwd=tmp_path, check=True,
    )

    status = vault_git.add_remote(tmp_path, "https://github.com/example/second.git")

    assert status == "exists"
    remotes = subprocess.run(
        ["git", "remote", "-v"], cwd=tmp_path, capture_output=True, text=True
    ).stdout
    assert "first.git" in remotes
    assert "second.git" not in remotes


def test_add_remote_fails_gracefully_outside_a_git_repo(tmp_path):
    assert vault_git.add_remote(tmp_path, "https://github.com/example/vault.git") == "failed"


class TestExpectedExitCodesAreNotErrors:
    """Two git commands answer with their exit status rather than failing.

    Logging them at ERROR is worse than noise: a real ingest printed two
    `CMD failed` lines while succeeding, which teaches a reader to ignore the
    level and sends anyone debugging after a non-problem.
    """

    def test_no_commits_yet_is_not_logged_as_a_failure(self, tmp_path, monkeypatch):
        """`artmind init` leaves a repo with no commits, so `git rev-parse HEAD`
        exits 128 on the very first ingest into a new vault."""
        import subprocess

        import artmind.vault_git as vg

        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        (tmp_path / ".artmind").mkdir()
        monkeypatch.setattr(vg, "ARTMIND_VAULT_DIR", tmp_path)

        seen = []
        monkeypatch.setattr(vg, "run_command", _recording(seen, returncode=128))

        assert vg.current_commit() is None
        assert seen[-1]["expected_codes"] == (128,), (
            "128 must be declared expected, or a new vault logs an ERROR on first ingest"
        )

    def test_having_changes_to_commit_is_not_logged_as_a_failure(self, tmp_path, monkeypatch):
        """`git diff --cached --quiet` exits 1 to mean "yes, there are staged
        changes" -- the success path for commit_paths."""
        import artmind.vault_git as vg

        seen = []
        monkeypatch.setattr(vg, "_vault_root", lambda: tmp_path)
        monkeypatch.setattr(vg, "run_command", _recording(seen, returncode=0))

        vg.commit_paths([tmp_path / "note.md"], "msg")

        diff_calls = [c for c in seen if "diff --cached" in c["cmd"]]
        assert diff_calls, "the staged-changes check did not run"
        assert diff_calls[0]["expected_codes"] == (1,), (
            "1 is the success path here; declaring it expected is what stops the "
            "ERROR line on every successful commit"
        )

    def test_no_configured_remote_is_not_logged_as_a_failure(self, tmp_path, monkeypatch):
        """`artmind init` never adds a remote, so `git push` with
        ARTMIND_VAULT_GIT_PUSH=1 set exits 128 ("No configured push
        destination") on every ingest into a brand-new, local-only vault --
        maybe_push already downgrades this to a WARNING; run_command must not
        also log it at ERROR first."""
        import artmind.vault_git as vg

        seen = []
        monkeypatch.setattr(vg, "load_env", lambda: {"ARTMIND_VAULT_GIT_PUSH": "1"})
        monkeypatch.setattr(vg, "_vault_root", lambda: tmp_path)
        monkeypatch.setattr(vg, "run_command", _recording(seen, returncode=128))

        vg.maybe_push()  # must not raise

        push_calls = [c for c in seen if c["cmd"] == "git push"]
        assert push_calls, "git push did not run"
        assert push_calls[0]["expected_codes"] == (128,), (
            "128 must be declared expected, or every ingest into a fresh vault "
            "with push enabled logs an ERROR for a normal, common state"
        )


def _recording(sink, returncode=0):
    def _run(cmd_str, timeout=None, cwd=None, extra_env=None, expected_codes=()):
        sink.append({"cmd": cmd_str, "expected_codes": expected_codes})
        return returncode, "", ""
    return _run
