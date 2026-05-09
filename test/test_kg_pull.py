"""Tests for artmind.kg_pull module."""

import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch, call

import pytest

from artmind.kg_pull import _rewrite_url_with_token, _detect_conflicts, pull_kg


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
