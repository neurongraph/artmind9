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
