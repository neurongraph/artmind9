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


import pytest

from artmind import obsidian_community as oc
from artmind.obsidian_community import FetchError

REGISTRY = json.dumps([
    {"id": "obsidian-git", "repo": "vinzent03/obsidian-git"},
    {"id": "unhide", "repo": "polyipseity/obsidian-unhide"},
]).encode()


def _asset(repo, name):
    return f"https://github.com/{repo}/releases/latest/download/{name}"


class FakeFetch:
    """URL -> bytes; anything else is a 404, like a release without styles.css."""

    def __init__(self, files):
        self.files, self.urls = files, []

    def __call__(self, url):
        self.urls.append(url)
        if url not in self.files:
            raise FetchError(url, "HTTP 404", missing=True)
        value = self.files[url]
        if isinstance(value, Exception):
            raise value
        return value


def _release(repo, *, styles=True):
    files = {
        _asset(repo, "main.js"): b"// main " + repo.encode(),
        _asset(repo, "manifest.json"): b'{"id": "x", "version": "1.0.0"}',
    }
    if styles:
        files[_asset(repo, "styles.css")] = b"/* css */"
    return files


def test_installs_release_files_and_enables(tmp_path):
    fetch = FakeFetch({oc.REGISTRY_URL: REGISTRY, **_release("vinzent03/obsidian-git")})

    results = oc.install_community_plugins(tmp_path, fetch=fetch, plugins=[{"id": "obsidian-git"}])

    assert results == [{"id": "obsidian-git", "status": "installed", "error": None}]
    dest = tmp_path / "plugins" / "obsidian-git"
    assert (dest / "main.js").read_bytes() == b"// main vinzent03/obsidian-git"
    assert (dest / "manifest.json").is_file()
    assert (dest / "styles.css").read_bytes() == b"/* css */"
    assert json.loads((tmp_path / "community-plugins.json").read_text()) == ["obsidian-git"]


def test_a_release_without_styles_css_still_installs(tmp_path):
    fetch = FakeFetch({oc.REGISTRY_URL: REGISTRY, **_release("polyipseity/obsidian-unhide", styles=False)})

    results = oc.install_community_plugins(tmp_path, fetch=fetch, plugins=[{"id": "unhide"}])

    assert results[0]["status"] == "installed"
    assert not (tmp_path / "plugins" / "unhide" / "styles.css").exists()


def test_seeds_data_json_only_when_absent(tmp_path):
    fetch = FakeFetch({oc.REGISTRY_URL: REGISTRY, **_release("polyipseity/obsidian-unhide")})
    plugins = [{"id": "unhide", "data": {"showHiddenFiles": True}}]

    oc.install_community_plugins(tmp_path, fetch=fetch, plugins=plugins)
    data_file = tmp_path / "plugins" / "unhide" / "data.json"
    assert json.loads(data_file.read_text()) == {"showHiddenFiles": True}

    data_file.write_text('{"showHiddenFiles": false}')
    oc.install_community_plugins(tmp_path, fetch=fetch, plugins=plugins)
    assert json.loads(data_file.read_text()) == {"showHiddenFiles": False}


def test_an_already_installed_plugin_is_not_downloaded_again(tmp_path):
    dest = tmp_path / "plugins" / "obsidian-git"
    dest.mkdir(parents=True)
    (dest / "manifest.json").write_text("{}")
    fetch = FakeFetch({})

    results = oc.install_community_plugins(tmp_path, fetch=fetch, plugins=[{"id": "obsidian-git"}])

    assert results == [{"id": "obsidian-git", "status": "present", "error": None}]
    assert fetch.urls == []
    assert json.loads((tmp_path / "community-plugins.json").read_text()) == ["obsidian-git"]


def test_an_unknown_id_fails_and_the_rest_continue(tmp_path):
    fetch = FakeFetch({oc.REGISTRY_URL: REGISTRY, **_release("vinzent03/obsidian-git")})

    results = oc.install_community_plugins(
        tmp_path, fetch=fetch, plugins=[{"id": "no-such-plugin"}, {"id": "obsidian-git"}]
    )

    assert results[0]["status"] == "failed"
    assert "no-such-plugin" in results[0]["error"]
    assert results[1]["status"] == "installed"
    assert json.loads((tmp_path / "community-plugins.json").read_text()) == ["obsidian-git"]


def test_a_network_failure_is_reported_not_raised(tmp_path):
    url = oc.REGISTRY_URL
    fetch = FakeFetch({url: FetchError(url, "timed out")})

    results = oc.install_community_plugins(tmp_path, fetch=fetch, plugins=[{"id": "obsidian-git"}])

    assert results[0]["status"] == "failed"
    assert "timed out" in results[0]["error"]
    assert not (tmp_path / "plugins" / "obsidian-git").exists()


def test_the_packaged_list_matches_the_my_work_vault():
    plugins = {p["id"]: p for p in oc.load_plugin_list()}

    assert list(plugins) == ["obsidian-git", "unhide", "vscode-editor", "ghostty-terminal"]
    assert plugins["unhide"]["data"]["showingRules"] == ["+/", r"-/\.git(?:\/|$)/u", r"-/\.venv(?:\/|$)/u"]
    assert {"json", "yaml", "env"} <= set(plugins["vscode-editor"]["data"]["extensions"])
    assert plugins["obsidian-git"]["data"]["syncMethod"] == "merge"


# ── parallel downloads and progress ───────────────────────────────────────────

import threading

REGISTRY3 = json.dumps([
    {"id": "obsidian-git", "repo": "vinzent03/obsidian-git"},
    {"id": "unhide", "repo": "polyipseity/obsidian-unhide"},
    {"id": "vscode-editor", "repo": "sunxvming/obsidian-vscode-editor"},
]).encode()
THREE = [{"id": "obsidian-git"}, {"id": "unhide"}, {"id": "vscode-editor"}]


def _three_releases():
    return {
        oc.REGISTRY_URL: REGISTRY3,
        **_release("vinzent03/obsidian-git"),
        **_release("polyipseity/obsidian-unhide"),
        **_release("sunxvming/obsidian-vscode-editor"),
    }


class GatedFetch(FakeFetch):
    """Holds the FIRST plugin's main.js until every other plugin's main.js has
    been fetched: completes out of order, and can only finish at all if the
    downloads really run concurrently."""

    def __init__(self, files, held_repo, others):
        super().__init__(files)
        self.held = _asset(held_repo, "main.js")
        self.others = {_asset(r, "main.js") for r in others}
        self.seen, self.release = set(), threading.Event()
        self.lock = threading.Lock()

    def __call__(self, url):
        if url == self.held:
            assert self.release.wait(timeout=5), "downloads ran one after another, not in parallel"
        result = super().__call__(url)
        with self.lock:
            if url in self.others:
                self.seen.add(url)
                if self.seen == self.others:
                    self.release.set()
        return result


def test_downloads_run_in_parallel_and_results_keep_list_order(tmp_path):
    fetch = GatedFetch(
        _three_releases(), "vinzent03/obsidian-git",
        ["polyipseity/obsidian-unhide", "sunxvming/obsidian-vscode-editor"],
    )

    results = oc.install_community_plugins(tmp_path, fetch=fetch, plugins=THREE)

    assert [r["id"] for r in results] == ["obsidian-git", "unhide", "vscode-editor"]
    assert all(r["status"] == "installed" for r in results)
    assert json.loads((tmp_path / "community-plugins.json").read_text()) == [
        "obsidian-git", "unhide", "vscode-editor",
    ]
    assert fetch.urls.count(oc.REGISTRY_URL) == 1


def test_progress_is_reported_before_each_download(tmp_path):
    messages = []
    fetch = FakeFetch(_three_releases())

    oc.install_community_plugins(tmp_path, fetch=fetch, plugins=THREE, echo=messages.append)

    assert messages == [
        "  plugin obsidian-git: downloading...",
        "  plugin unhide: downloading...",
        "  plugin vscode-editor: downloading...",
    ]


def test_no_progress_and_no_network_when_everything_is_present(tmp_path):
    for entry in THREE:
        dest = tmp_path / "plugins" / entry["id"]
        dest.mkdir(parents=True)
        (dest / "manifest.json").write_text("{}")
    messages, fetch = [], FakeFetch({})

    results = oc.install_community_plugins(tmp_path, fetch=fetch, plugins=THREE, echo=messages.append)

    assert [r["status"] for r in results] == ["present"] * 3
    assert messages == [] and fetch.urls == []


def test_one_failed_download_does_not_stop_the_others(tmp_path):
    files = _three_releases()
    broken = _asset("polyipseity/obsidian-unhide", "main.js")
    files[broken] = FetchError(broken, "connection reset")

    results = oc.install_community_plugins(tmp_path, fetch=FakeFetch(files), plugins=THREE)

    assert [r["status"] for r in results] == ["installed", "failed", "installed"]
    assert "connection reset" in results[1]["error"]
    assert not (tmp_path / "plugins" / "unhide").exists()
    assert json.loads((tmp_path / "community-plugins.json").read_text()) == ["obsidian-git", "vscode-editor"]


# ── core plugins ──────────────────────────────────────────────────────────────


def test_core_plugins_are_added_to_a_fresh_vault(tmp_path):
    assert oc.enable_core_plugins(tmp_path) == {"webviewer": "added"}

    assert json.loads((tmp_path / "core-plugins.json").read_text()) == {"webviewer": True}


def test_core_plugins_keep_every_other_setting(tmp_path):
    (tmp_path / "core-plugins.json").write_text('{"graph": false, "file-explorer": true}')

    assert oc.enable_core_plugins(tmp_path) == {"webviewer": "added"}

    assert json.loads((tmp_path / "core-plugins.json").read_text()) == {
        "graph": False, "file-explorer": True, "webviewer": True,
    }


def test_a_committed_choice_to_keep_webviewer_off_wins(tmp_path):
    (tmp_path / "core-plugins.json").write_text('{"webviewer": false}')

    assert oc.enable_core_plugins(tmp_path) == {"webviewer": "off"}

    assert json.loads((tmp_path / "core-plugins.json").read_text()) == {"webviewer": False}


def test_core_plugins_already_on_rewrite_nothing(tmp_path):
    path = tmp_path / "core-plugins.json"
    path.write_text('{"webviewer":true}')

    assert oc.enable_core_plugins(tmp_path) == {"webviewer": "already"}
    assert path.read_text() == '{"webviewer":true}'


def test_core_plugins_legacy_list_form_is_appended(tmp_path):
    (tmp_path / "core-plugins.json").write_text('["file-explorer"]')

    assert oc.enable_core_plugins(tmp_path) == {"webviewer": "added"}

    assert json.loads((tmp_path / "core-plugins.json").read_text()) == ["file-explorer", "webviewer"]


def test_an_unreadable_core_plugins_file_is_left_alone(tmp_path):
    (tmp_path / "core-plugins.json").write_text("not json")

    assert oc.enable_core_plugins(tmp_path) == {"webviewer": "failed"}
    assert (tmp_path / "core-plugins.json").read_text() == "not json"
