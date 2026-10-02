"""Obsidian community plugins for `artmind vault new` (spec 2026-10-02 §3)."""
from __future__ import annotations

import json

from artmind.setup import enable_obsidian_plugin


def test_enable_appends_the_id_once(tmp_path):
    (tmp_path / "community-plugins.json").write_text('["obsidian-git"]')

    assert enable_obsidian_plugin(tmp_path, "unhide") == "added"
    assert enable_obsidian_plugin(tmp_path, "unhide") == "already"

    assert json.loads((tmp_path / "community-plugins.json").read_text()) == ["obsidian-git", "unhide"]


def test_enable_creates_the_file_when_absent(tmp_path):
    assert enable_obsidian_plugin(tmp_path, "artmind") == "added"

    assert json.loads((tmp_path / "community-plugins.json").read_text()) == ["artmind"]


def test_enable_leaves_an_unreadable_file_alone(tmp_path):
    (tmp_path / "community-plugins.json").write_text('{"not": "a list"}')

    assert enable_obsidian_plugin(tmp_path, "artmind") == "failed"

    assert (tmp_path / "community-plugins.json").read_text() == '{"not": "a list"}'
