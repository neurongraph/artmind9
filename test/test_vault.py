"""Vault discovery and layout (docs/vault.md)."""
from __future__ import annotations

from pathlib import Path

import pytest

from artmind import vault


def test_finds_vault_in_the_directory_itself(tmp_path):
    (tmp_path / ".artmind").mkdir()
    assert vault.find_vault(tmp_path) == tmp_path.resolve()


def test_walks_up_to_find_the_vault(tmp_path):
    (tmp_path / ".artmind").mkdir()
    deep = tmp_path / "notes" / "2026" / "august"
    deep.mkdir(parents=True)

    assert vault.find_vault(deep) == tmp_path.resolve()


def test_returns_none_outside_any_vault(tmp_path):
    assert vault.find_vault(tmp_path) is None


def test_innermost_vault_wins(tmp_path):
    """Nested vaults behave like nested git repos."""
    (tmp_path / ".artmind").mkdir()
    inner = tmp_path / "inner"
    (inner / ".artmind").mkdir(parents=True)

    assert vault.find_vault(inner) == inner.resolve()


def test_a_file_named_dot_artmind_is_not_a_vault(tmp_path):
    (tmp_path / ".artmind").write_text("not a directory")
    assert vault.find_vault(tmp_path) is None


def test_explicit_path_beats_everything(tmp_path, monkeypatch):
    explicit = tmp_path / "explicit"
    (explicit / ".artmind").mkdir(parents=True)
    other = tmp_path / "other"
    (other / ".artmind").mkdir(parents=True)
    monkeypatch.setenv("ARTMIND_VAULT", str(other))
    monkeypatch.chdir(other)

    assert vault.resolve_vault(str(explicit)) == explicit.resolve()


def test_env_var_beats_the_walk_up(tmp_path, monkeypatch):
    """ARTMIND_VAULT exists for cron and anything with no meaningful cwd."""
    env_vault = tmp_path / "env"
    (env_vault / ".artmind").mkdir(parents=True)
    cwd_vault = tmp_path / "cwd"
    (cwd_vault / ".artmind").mkdir(parents=True)
    monkeypatch.setenv("ARTMIND_VAULT", str(env_vault))
    monkeypatch.chdir(cwd_vault)

    assert vault.resolve_vault() == env_vault.resolve()


def test_falls_back_to_the_walk_up(tmp_path, monkeypatch):
    cwd_vault = tmp_path / "cwd"
    (cwd_vault / ".artmind").mkdir(parents=True)
    monkeypatch.delenv("ARTMIND_VAULT", raising=False)
    monkeypatch.chdir(cwd_vault)

    assert vault.resolve_vault() == cwd_vault.resolve()


def test_resolves_to_none_outside_any_vault(tmp_path, monkeypatch):
    monkeypatch.delenv("ARTMIND_VAULT", raising=False)
    monkeypatch.chdir(tmp_path)

    assert vault.resolve_vault() is None


def test_an_env_var_pointing_at_a_non_vault_is_refused(tmp_path, monkeypatch):
    """Silently falling back to the walk-up would ingest into the wrong vault."""
    monkeypatch.setenv("ARTMIND_VAULT", str(tmp_path / "nope"))

    with pytest.raises(vault.VaultError, match="ARTMIND_VAULT"):
        vault.resolve_vault()
