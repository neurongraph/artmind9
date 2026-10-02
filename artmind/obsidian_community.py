"""Install Obsidian community plugins into a vault (`artmind vault new`, spec
docs/superpowers/specs/2026-10-02-vault-new-design.md §3).

The plugin list is package data (`obsidian/community_plugins.yaml`): ids plus
optional seed settings. Each id is resolved to its GitHub repo through
Obsidian's official registry, and the latest release's assets are written to
`.obsidian/plugins/<id>/` -- exactly what Obsidian's own installer does. One
plugin failing (offline, renamed) never stops the others. Core plugins in
`CORE_PLUGINS` are switched on through `core-plugins.json`.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import yaml

REGISTRY_URL = "https://raw.githubusercontent.com/obsidianmd/obsidian-releases/master/community-plugins.json"
ASSET_URL = "https://github.com/{repo}/releases/latest/download/{name}"
REQUIRED_ASSETS = ("main.js", "manifest.json")
OPTIONAL_ASSETS = ("styles.css",)
PLUGINS_FILE = Path(__file__).parent / "obsidian" / "community_plugins.yaml"
# Core (built-in) plugins `vault new` turns on. Web Viewer is off by default.
CORE_PLUGINS = ("webviewer",)


class FetchError(Exception):
    """A download failed. `missing` is True for a 404: the file does not exist."""

    def __init__(self, url: str, reason: str, missing: bool = False):
        super().__init__(f"{url}: {reason}")
        self.missing = missing


def default_fetch(url: str) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        raise FetchError(url, f"HTTP {e.code}", missing=e.code == 404) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError(url, str(getattr(e, "reason", e))) from e


def load_plugin_list(path: Path | None = None) -> list[dict]:
    return yaml.safe_load(Path(path or PLUGINS_FILE).read_text(encoding="utf-8")) or []


def install_community_plugins(
    obsidian_dir: Path,
    *,
    fetch: Callable[[str], bytes] = default_fetch,
    plugins: list[dict] | None = None,
    echo: Callable[[str], None] | None = None,
    max_workers: int = 4,
) -> list[dict]:
    """Install, seed and enable each plugin in `obsidian_dir` (a vault's `.obsidian/`).

    Returns one `{id, status, error}` per plugin, in list order. status is
    "installed", "present" (already in `.obsidian/plugins/`, left as is -- a
    clone carries the plugins laptop A committed) or "failed" (with the reason
    in error). A plugin's `data.json` is seeded only when absent.

    Downloads run in parallel: GitHub's asset CDN can take ~20s just to accept
    a connection, so one plugin after another made this step take minutes.
    Writing files and enabling stay sequential and in list order --
    `community-plugins.json` is a read-modify-write file. `echo` gets one
    "downloading" line per plugin before its download starts.
    """
    from artmind.setup import enable_obsidian_plugin

    obsidian_dir = Path(obsidian_dir)
    plugins = load_plugin_list() if plugins is None else plugins
    echo = echo or (lambda _message: None)
    to_fetch = [e["id"] for e in plugins if not (obsidian_dir / "plugins" / e["id"] / "manifest.json").is_file()]
    downloads: dict[str, dict[str, bytes] | Exception] = {}
    if to_fetch:
        try:
            registry = {p["id"]: p["repo"] for p in json.loads(fetch(REGISTRY_URL))}
        except (FetchError, ValueError) as e:
            downloads = {plugin_id: e for plugin_id in to_fetch}
        else:
            for plugin_id in to_fetch:
                echo(f"  plugin {plugin_id}: downloading...")
            with ThreadPoolExecutor(max_workers=min(max_workers, len(to_fetch))) as pool:
                futures = {pid: pool.submit(_fetch_plugin, pid, registry, fetch) for pid in to_fetch}
                for plugin_id, future in futures.items():
                    try:
                        downloads[plugin_id] = future.result()
                    except (FetchError, ValueError) as e:
                        downloads[plugin_id] = e

    results = []
    for entry in plugins:
        plugin_id = entry["id"]
        dest = obsidian_dir / "plugins" / plugin_id
        result = {"id": plugin_id, "status": "present", "error": None}
        if plugin_id in downloads:
            files = downloads[plugin_id]
            if isinstance(files, Exception):
                results.append({**result, "status": "failed", "error": str(files)})
                continue
            dest.mkdir(parents=True, exist_ok=True)
            for name, data in files.items():
                (dest / name).write_bytes(data)
            result["status"] = "installed"
        data_file = dest / "data.json"
        if entry.get("data") is not None and not data_file.exists():
            data_file.write_text(json.dumps(entry["data"], indent=2), encoding="utf-8")
        if enable_obsidian_plugin(obsidian_dir, plugin_id) == "failed":
            result["error"] = "could not update community-plugins.json -- enable it in Obsidian's settings"
        results.append(result)
    return results


def enable_core_plugins(obsidian_dir: Path, ids: tuple[str, ...] = CORE_PLUGINS) -> dict[str, str]:
    """Turn on Obsidian core plugins through `.obsidian/core-plugins.json`.

    Since Obsidian 1.4 that file maps id -> bool, and a core plugin it does not
    list gets its built-in default (checked against Obsidian 1.13.7's
    loader), so adding only these ids leaves every other core plugin exactly as
    Obsidian would have it -- the file can be written before Obsidian ever
    opens the vault. An id already listed is left alone, so a choice committed
    by another machine (`"webviewer": false`) wins. The legacy list form gets
    the ids appended.

    Returns `{id: "added" | "already" | "off" | "failed"}` ("off": listed as
    disabled, kept; "failed": the file is unreadable, left alone).
    """
    path = Path(obsidian_dir) / "core-plugins.json"
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(current, (dict, list)):
            raise ValueError("neither an object nor a list")
    except (OSError, ValueError):
        return {plugin_id: "failed" for plugin_id in ids}
    status: dict[str, str] = {}
    for plugin_id in ids:
        if isinstance(current, dict):
            if plugin_id in current:
                status[plugin_id] = "already" if current[plugin_id] else "off"
            else:
                current[plugin_id] = True
                status[plugin_id] = "added"
        elif plugin_id in current:
            status[plugin_id] = "already"
        else:
            current.append(plugin_id)
            status[plugin_id] = "added"
    if "added" in status.values():
        path.write_text(json.dumps(current, indent=2), encoding="utf-8")
    return status


def _fetch_plugin(plugin_id: str, registry: dict[str, str], fetch: Callable[[str], bytes]) -> dict[str, bytes]:
    repo = registry.get(plugin_id)
    if repo is None:
        raise FetchError(REGISTRY_URL, f"no plugin {plugin_id!r} in Obsidian's registry")
    return _download(repo, fetch)


def _download(repo: str, fetch: Callable[[str], bytes]) -> dict[str, bytes]:
    """All of a release's assets, fetched in parallel (each one is a fresh,
    possibly slow, CDN connection) and all before anything is written, so a
    failure never leaves a half-installed plugin behind. A missing optional
    asset (a release without styles.css) is skipped."""
    names = REQUIRED_ASSETS + OPTIONAL_ASSETS
    with ThreadPoolExecutor(max_workers=len(names)) as pool:
        futures = {name: pool.submit(fetch, ASSET_URL.format(repo=repo, name=name)) for name in names}
        files: dict[str, bytes] = {}
        for name, future in futures.items():
            try:
                files[name] = future.result()
            except FetchError as e:
                if name in REQUIRED_ASSETS or not e.missing:
                    raise
    return files
