"""Install Obsidian community plugins into a vault (`artmind vault new`, spec
docs/superpowers/specs/2026-10-02-vault-new-design.md §3).

The plugin list is package data (`obsidian/community_plugins.yaml`): ids plus
optional seed settings. Each id is resolved to its GitHub repo through
Obsidian's official registry, and the latest release's assets are written to
`.obsidian/plugins/<id>/` -- exactly what Obsidian's own installer does. One
plugin failing (offline, renamed) never stops the others.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

import yaml

REGISTRY_URL = "https://raw.githubusercontent.com/obsidianmd/obsidian-releases/master/community-plugins.json"
ASSET_URL = "https://github.com/{repo}/releases/latest/download/{name}"
REQUIRED_ASSETS = ("main.js", "manifest.json")
OPTIONAL_ASSETS = ("styles.css",)
PLUGINS_FILE = Path(__file__).parent / "obsidian" / "community_plugins.yaml"


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
) -> list[dict]:
    """Install, seed and enable each plugin in `obsidian_dir` (a vault's `.obsidian/`).

    Returns one `{id, status, error}` per plugin. status is "installed",
    "present" (already in `.obsidian/plugins/`, left as is -- a clone carries
    the plugins laptop A committed) or "failed" (with the reason in error).
    A plugin's `data.json` is seeded only when absent.
    """
    from artmind.setup import enable_obsidian_plugin

    obsidian_dir = Path(obsidian_dir)
    plugins = load_plugin_list() if plugins is None else plugins
    registry: dict[str, str] | None = None
    results = []
    for entry in plugins:
        plugin_id = entry["id"]
        dest = obsidian_dir / "plugins" / plugin_id
        result = {"id": plugin_id, "status": "present", "error": None}
        if not (dest / "manifest.json").is_file():
            try:
                if registry is None:
                    registry = {p["id"]: p["repo"] for p in json.loads(fetch(REGISTRY_URL))}
                repo = registry.get(plugin_id)
                if repo is None:
                    raise FetchError(REGISTRY_URL, f"no plugin {plugin_id!r} in Obsidian's registry")
                files = _download(repo, fetch)
            except (FetchError, ValueError) as e:
                results.append({**result, "status": "failed", "error": str(e)})
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


def _download(repo: str, fetch: Callable[[str], bytes]) -> dict[str, bytes]:
    """All of a release's assets, fetched before anything is written, so a
    failure never leaves a half-installed plugin behind."""
    files = {name: fetch(ASSET_URL.format(repo=repo, name=name)) for name in REQUIRED_ASSETS}
    for name in OPTIONAL_ASSETS:
        try:
            files[name] = fetch(ASSET_URL.format(repo=repo, name=name))
        except FetchError as e:
            if not e.missing:
                raise
    return files
