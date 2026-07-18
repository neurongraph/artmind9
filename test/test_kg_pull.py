"""Tests for artmind.kg_pull module."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, call

import pytest

from artmind.kg_pull import (
    _rewrite_url_with_token,
    _detect_conflicts,
    _reject_leading_dash,
    _run_git,
    _sparse_clone,
    _GIT_ALLOWED_PROTOCOLS,
    pull_kg,
)


class TestRewriteUrlWithToken:
    def test_noop_when_no_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        assert _rewrite_url_with_token("git@github.com:acme/repo.git") == "git@github.com:acme/repo.git"

    def test_noop_for_ssh_url_even_with_token(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc123")
        assert _rewrite_url_with_token("git@github.com:acme/repo.git") == "git@github.com:acme/repo.git"

    def test_injects_token_into_https_url(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc123")
        result = _rewrite_url_with_token("https://github.com/acme/repo.git")
        assert result == "https://ghp_abc123@github.com/acme/repo.git"

    def test_noop_for_https_when_no_token(self, monkeypatch):
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        assert _rewrite_url_with_token("https://github.com/acme/repo.git") == "https://github.com/acme/repo.git"


class TestDetectConflicts:
    def test_no_conflicts_when_target_empty(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        incoming = ["doc_a", "doc_b"]
        assert _detect_conflicts(incoming, target) == []

    def test_detects_existing_folder(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        (target / "doc_a").mkdir()
        incoming = ["doc_a", "doc_b"]
        assert _detect_conflicts(incoming, target) == ["doc_a"]

    def test_no_conflicts_when_target_does_not_exist(self, tmp_path):
        target = tmp_path / "nonexistent"
        incoming = ["doc_a"]
        assert _detect_conflicts(incoming, target) == []


class TestPullKg:
    def _make_fake_repo(self, tmp_path, doc_names: list[str]) -> Path:
        """Create a fake 'checked-out' repo dir with document sub-folders."""
        repo_content = tmp_path / "repo_content"
        for name in doc_names:
            doc_dir = repo_content / name
            doc_dir.mkdir(parents=True)
            (doc_dir / "document.json").write_text(json.dumps({"id": name}))
            (doc_dir / "entities.json").write_text("[]")
        return repo_content

    @patch("artmind.kg_pull._sparse_clone")
    def test_pulls_documents_into_target(self, mock_clone, tmp_path, monkeypatch):
        import artmind.kg_pull as mod
        kg_dir = tmp_path / "kg"
        kg_dir.mkdir()
        monkeypatch.setattr(mod, "KG_DIR", kg_dir)

        fake_content = self._make_fake_repo(tmp_path, ["doc_a", "doc_b"])
        cleanup_dir = tmp_path / "cleanup"
        cleanup_dir.mkdir()
        mock_clone.return_value = (fake_content, cleanup_dir)

        result = pull_kg("https://github.com/acme/repo.git", "data/kg/sales", "sales")

        assert result["pulled_count"] == 2
        assert result["conflicts"] == []
        assert (kg_dir / "sales" / "doc_a" / "document.json").exists()
        assert (kg_dir / "sales" / "doc_b" / "document.json").exists()

    @patch("artmind.kg_pull._sparse_clone")
    def test_aborts_on_conflict(self, mock_clone, tmp_path, monkeypatch):
        import artmind.kg_pull as mod
        kg_dir = tmp_path / "kg"
        target = kg_dir / "sales"
        target.mkdir(parents=True)
        (target / "doc_a").mkdir()
        (target / "doc_a" / "document.json").write_text("{}")
        monkeypatch.setattr(mod, "KG_DIR", kg_dir)

        fake_content = self._make_fake_repo(tmp_path, ["doc_a", "doc_b"])
        cleanup_dir = tmp_path / "cleanup"
        cleanup_dir.mkdir()
        mock_clone.return_value = (fake_content, cleanup_dir)

        with pytest.raises(RuntimeError, match="conflict"):
            pull_kg("https://github.com/acme/repo.git", "data/kg/sales", "sales")

        # doc_b should NOT have been copied since the whole pull aborted
        assert not (target / "doc_b").exists()

    @patch("artmind.kg_pull._sparse_clone")
    def test_error_when_no_documents_found(self, mock_clone, tmp_path, monkeypatch):
        import artmind.kg_pull as mod
        kg_dir = tmp_path / "kg"
        kg_dir.mkdir()
        monkeypatch.setattr(mod, "KG_DIR", kg_dir)

        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        cleanup_dir = tmp_path / "cleanup"
        cleanup_dir.mkdir()
        mock_clone.return_value = (empty_dir, cleanup_dir)

        with pytest.raises(RuntimeError, match="No document"):
            pull_kg("https://github.com/acme/repo.git", "data/kg/sales", "sales")


class TestRunGitProtocolAllowlist:
    """git's own documented mitigation (GIT_ALLOW_PROTOCOL / `git help clone`) for the
    ext:: transport RCE — verified against `git help -c protocol.allow` semantics:
    a colon-separated allowlist of transport names disables every protocol not
    listed, including `ext`, `file`, and `fd`.
    """

    @patch("artmind.kg_pull.subprocess.run")
    def test_sets_git_allow_protocol_env_var(self, mock_run):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"], returncode=0, stdout="", stderr=""
        )
        _run_git(["status"])

        assert mock_run.called
        _, kwargs = mock_run.call_args
        assert "env" in kwargs
        assert kwargs["env"]["GIT_ALLOW_PROTOCOL"] == "https:ssh:git"
        # ext/file/fd/http must not appear anywhere in the allowlist string —
        # http is excluded too: it's unencrypted and no legitimate use case
        # for it exists in this codebase (see _GIT_ALLOWED_PROTOCOLS comment).
        allowlist = kwargs["env"]["GIT_ALLOW_PROTOCOL"].split(":")
        assert "ext" not in allowlist
        assert "file" not in allowlist
        assert "fd" not in allowlist
        assert "http" not in allowlist

    @patch("artmind.kg_pull.subprocess.run")
    def test_preserves_rest_of_environment(self, mock_run, monkeypatch):
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        monkeypatch.setenv("SOME_OTHER_VAR", "keep-me")
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"], returncode=0, stdout="", stderr=""
        )
        _run_git(["status"])

        _, kwargs = mock_run.call_args
        assert kwargs["env"]["PATH"] == "/usr/bin:/bin"
        assert kwargs["env"]["SOME_OTHER_VAR"] == "keep-me"

    @patch("artmind.kg_pull.subprocess.run")
    def test_module_constant_matches_env_value(self, mock_run):
        # Deliberately separate from test_sets_git_allow_protocol_env_var above:
        # that test pins the exact allowlist value (catches accidental drift in
        # what's allowed), while this one only checks _run_git actually uses the
        # module constant rather than a hardcoded copy (catches the env-wiring
        # bug independent of what the constant's value happens to be).
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "status"], returncode=0, stdout="", stderr=""
        )
        _run_git(["status"])
        _, kwargs = mock_run.call_args
        assert kwargs["env"]["GIT_ALLOW_PROTOCOL"] == _GIT_ALLOWED_PROTOCOLS


class TestRejectLeadingDash:
    def test_rejects_dash_prefixed_value(self):
        with pytest.raises(RuntimeError, match="must not start with"):
            _reject_leading_dash("-oProxyCommand=whoami", "repo URL")

    def test_allows_normal_value(self):
        _reject_leading_dash("https://github.com/acme/repo.git", "repo URL")  # no raise

    def test_allows_ssh_style_value(self):
        _reject_leading_dash("git@github.com:acme/repo.git", "repo URL")  # no raise


class TestSparseCloneValidation:
    @patch("artmind.kg_pull._run_git")
    def test_rejects_leading_dash_repo_url_before_any_git_call(self, mock_run_git):
        with pytest.raises(RuntimeError, match="repo URL"):
            _sparse_clone("--upload-pack=touch /tmp/pwned", "data/kg/sales")
        mock_run_git.assert_not_called()

    @patch("artmind.kg_pull._run_git")
    def test_rejects_leading_dash_repo_path_before_any_git_call(self, mock_run_git):
        with pytest.raises(RuntimeError, match="repo path"):
            _sparse_clone("https://github.com/acme/repo.git", "-oProxyCommand=whoami")
        mock_run_git.assert_not_called()

    @patch("artmind.kg_pull._run_git")
    def test_rejects_repo_path_escaping_clone_dir(self, mock_run_git):
        # _run_git is mocked out (no real git), but _sparse_clone still creates
        # tmp_dir/repo itself via mkdtemp, so build a clone_dir there and place
        # a sentinel *outside* it to simulate a traversal target.
        with pytest.raises(RuntimeError, match="escapes the cloned repository"):
            _sparse_clone("https://github.com/acme/repo.git", "../../../etc")
        mock_run_git.assert_called()  # got past validation, into the (mocked) clone calls

    @patch("artmind.kg_pull._run_git")
    def test_allows_legitimate_nested_repo_path(self, mock_run_git, tmp_path, monkeypatch):
        # Simulate the real filesystem effect of the (mocked) git calls: create
        # the nested content dir under wherever _sparse_clone's mkdtemp lands.
        created = {}

        def fake_mkdtemp(prefix=None):
            base = tmp_path / "artmind_pull_fake"
            base.mkdir(exist_ok=True)
            clone_dir = base / "repo" / "data" / "kg" / "sales"
            clone_dir.mkdir(parents=True, exist_ok=True)
            created["base"] = str(base)
            return str(base)

        monkeypatch.setattr("artmind.kg_pull.tempfile.mkdtemp", fake_mkdtemp)

        content_dir, tmp_dir = _sparse_clone(
            "https://github.com/acme/repo.git", "data/kg/sales"
        )
        assert content_dir == Path(created["base"]) / "repo" / "data" / "kg" / "sales"
        assert content_dir.is_dir()
        shutil.rmtree(tmp_dir, ignore_errors=True)
