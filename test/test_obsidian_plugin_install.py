"""`artmind init` installs the packaged Obsidian plugin into the vault.

Driven against a stand-in build folder (the real one is a gitignored build
output), with assertions on the files actually written.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

import artmind.setup as artmind_setup
from artmind import vault
from artmind.cli import cli
from artmind.setup import OBSIDIAN_PLUGIN_FILES, install_obsidian_plugin

PLUGIN_SOURCE = Path(__file__).resolve().parent.parent / "obsidian" / "artmind-obsidian"


def _build(folder: Path, version: str = "0.2.0", main: str = "/* main */") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "main.js").write_text(main)
    (folder / "manifest.json").write_text(json.dumps({"id": "artmind", "version": version}))
    (folder / "styles.css").write_text(".artmind {}")
    return folder


@pytest.fixture
def obsidian_vault(tmp_path) -> Path:
    root = tmp_path / "vault"
    (root / ".obsidian").mkdir(parents=True)
    return root


def _installed(root: Path) -> Path:
    return root / ".obsidian" / "plugins" / "artmind"


def test_installs_the_three_files_and_enables_the_plugin(obsidian_vault, tmp_path):
    result = install_obsidian_plugin(obsidian_vault, _build(tmp_path / "build"))

    assert result == {"status": "installed", "version": "0.2.0", "previous": None, "enabled": "added"}
    assert sorted(p.name for p in _installed(obsidian_vault).iterdir()) == sorted(OBSIDIAN_PLUGIN_FILES)
    assert json.loads((obsidian_vault / ".obsidian" / "community-plugins.json").read_text()) == ["artmind"]


def test_a_rerun_updates_changed_files_and_keeps_settings_and_other_plugins(obsidian_vault, tmp_path):
    install_obsidian_plugin(obsidian_vault, _build(tmp_path / "v1", version="0.1.0"))
    (_installed(obsidian_vault) / "data.json").write_text('{"artmindPath": "/mine"}')
    (obsidian_vault / ".obsidian" / "community-plugins.json").write_text('["obsidian-git", "artmind"]')

    result = install_obsidian_plugin(obsidian_vault, _build(tmp_path / "v2", version="0.2.0", main="/* new */"))

    assert (result["status"], result["previous"], result["version"], result["enabled"]) == ("updated", "0.1.0", "0.2.0", "already")
    assert (_installed(obsidian_vault) / "main.js").read_text() == "/* new */"
    assert (_installed(obsidian_vault) / "data.json").read_text() == '{"artmindPath": "/mine"}'
    assert json.loads((obsidian_vault / ".obsidian" / "community-plugins.json").read_text()) == ["obsidian-git", "artmind"]


def test_an_identical_build_is_current_and_writes_nothing(obsidian_vault, tmp_path):
    build = _build(tmp_path / "build")
    install_obsidian_plugin(obsidian_vault, build)
    main = _installed(obsidian_vault) / "main.js"
    before = main.stat().st_mtime_ns

    assert install_obsidian_plugin(obsidian_vault, build)["status"] == "current"
    assert main.stat().st_mtime_ns == before


def test_enabling_appends_to_the_plugins_already_on(obsidian_vault, tmp_path):
    (obsidian_vault / ".obsidian" / "community-plugins.json").write_text('["obsidian-git"]')
    install_obsidian_plugin(obsidian_vault, _build(tmp_path / "build"))
    assert json.loads((obsidian_vault / ".obsidian" / "community-plugins.json").read_text()) == ["obsidian-git", "artmind"]


def test_an_unreadable_enabled_list_is_left_alone(obsidian_vault, tmp_path):
    enabled = obsidian_vault / ".obsidian" / "community-plugins.json"
    enabled.write_text("{not json")

    result = install_obsidian_plugin(obsidian_vault, _build(tmp_path / "build"))

    assert (result["status"], result["enabled"]) == ("installed", "failed")
    assert enabled.read_text() == "{not json"


def test_nothing_without_obsidian(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    assert install_obsidian_plugin(root, _build(tmp_path / "build"))["status"] == "no_obsidian"
    assert not (root / ".obsidian").exists(), "creating Obsidian's config is Obsidian's job"


def test_nothing_from_a_build_without_the_plugin(obsidian_vault, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert install_obsidian_plugin(obsidian_vault, empty)["status"] == "not_packaged"
    assert not _installed(obsidian_vault).exists()


def test_the_vault_gitignores_the_plugin_folder():
    assert "/.obsidian/plugins/artmind/\n" in vault.GITIGNORE_BLOCK


def test_init_installs_it_and_says_so(obsidian_vault, tmp_path, monkeypatch):
    monkeypatch.setattr(artmind_setup, "PACKAGE_OBSIDIAN_PLUGIN_DIR", _build(tmp_path / "build"))

    result = CliRunner().invoke(cli, ["init", str(obsidian_vault)])

    assert result.exit_code == 0, result.output
    assert "Plugin:   artmind 0.2.0 installed and enabled" in result.output
    assert (_installed(obsidian_vault) / "main.js").is_file()


def test_the_staged_build_matches_the_plugin_source():
    """A stale staged build (artmind/obsidian_plugin/) would install an old
    plugin silently. Only checked where a build has been staged."""
    staged = artmind_setup.PACKAGE_OBSIDIAN_PLUGIN_DIR / "manifest.json"
    if not staged.is_file():
        pytest.skip("no staged plugin build (just obsidian-plugin-build)")
    assert json.loads(staged.read_text()) == json.loads((PLUGIN_SOURCE / "manifest.json").read_text())
