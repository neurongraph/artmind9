"""Wheel packaging: every artmind subpackage and the web UI assets must ship.

An explicit `packages = ["artmind", "utils"]` list once silently dropped
artmind.webui/traces/kg_views/... from the wheel while the editable dev install
(which reads the checkout) hid it. These tests drive setuptools' own discovery
against the checkout, so they need no wheel build and no network.
"""
import tomllib
from pathlib import Path

from setuptools import find_packages

_ROOT = Path(__file__).resolve().parent.parent
_CFG = tomllib.loads((_ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]


def _discovered() -> set[str]:
    find = _CFG["packages"]["find"]  # a literal `packages = [...]` list would fail here
    return set(find_packages(
        where=str(_ROOT), include=find["include"], exclude=find.get("exclude", ())
    ))


def test_packages_are_discovered_not_listed():
    assert isinstance(_CFG["packages"], dict) and "find" in _CFG["packages"]
    assert _CFG["py-modules"] == ["paths"]


def test_every_on_disk_subpackage_is_discovered():
    on_disk = {
        ".".join(init.parent.relative_to(_ROOT).parts)
        for top in ("artmind", "utils")
        for init in (_ROOT / top).rglob("__init__.py")
    }
    assert on_disk, "found no packages -- test is mis-rooted"
    assert on_disk <= _discovered()
    assert {"artmind.webui", "artmind.webui.backends", "artmind.traces",
            "artmind.kg_views", "utils"} <= _discovered()


def test_only_artmind_and_utils_are_packaged():
    assert {p.split(".")[0] for p in _discovered()} == {"artmind", "utils"}


def test_package_data_globs_cover_webui_assets():
    globs = _CFG["package-data"]["artmind"]
    pkg = _ROOT / "artmind"
    assets = [p for p in (pkg / "webui").rglob("*")
              if p.is_file() and p.suffix != ".py" and p.name != ".gitkeep"
              and "__pycache__" not in p.parts]
    assert assets, "found no webui assets -- test is mis-rooted"
    for asset in assets:
        rel = asset.relative_to(pkg)
        assert any(rel.full_match(g) for g in globs), rel
    for needed in ("skills/**/*", "domains/schemas/**/*", "domains/meta.yaml",
                   "opencode/**/*", "env.example"):
        assert needed in globs
