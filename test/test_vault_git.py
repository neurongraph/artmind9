"""Tests for artmind.vault_git: read-only helpers (spec 2026-09-26, D1)."""

import subprocess

import pytest

import artmind.vault_git as vault_git


def _init_git_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _commit(path, message):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", message], cwd=path, check=True)


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    _init_git_repo(v)
    monkeypatch.setattr(vault_git, "ARTMIND_VAULT_DIR", v)
    return v


# ── is_dirty ─────────────────────────────────────────────────────────────────


def test_is_dirty_false_on_a_clean_vault(vault):
    f = vault / "doc.md"
    f.write_text("hello\n")
    _commit(vault, "seed")
    assert vault_git.is_dirty() is False


def test_is_dirty_true_with_uncommitted_changes(vault):
    f = vault / "doc.md"
    f.write_text("hello\n")
    _commit(vault, "seed")
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


class TestExpectedExitCodesAreNotErrors:
    """`git rev-parse HEAD` answers with its exit status rather than failing.

    Logging it at ERROR is worse than noise: a real ingest printed a
    `CMD failed` line while succeeding, which teaches a reader to ignore the
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


def _recording(sink, returncode=0):
    def _run(cmd, timeout=None, cwd=None, extra_env=None, expected_codes=()):
        sink.append({"cmd": cmd, "expected_codes": expected_codes})
        return returncode, "", ""
    return _run
