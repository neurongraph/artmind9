"""paths.py deriving from a vault, and falling back when there is none.

Run in subprocesses because paths.py computes its constants at import; a
monkeypatched reload would not exercise the real code path.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PROBE = (
    "import json, paths;"
    "print(json.dumps({"
    "'vault': str(paths.ARTMIND_VAULT_DIR) if paths.ARTMIND_VAULT_DIR else None,"
    "'home': str(paths.ARTMIND_HOME),"
    "'data': str(paths.ARTMIND_DATA_DIR),"
    "'schemas': str(paths.DOMAIN_SCHEMAS_DIR),"
    "'kg': str(paths.KG_DIR),"
    "}))"
)


def _probe(cwd: Path, **env_overrides) -> dict:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    for key in ("ARTMIND_HOME", "ARTMIND_DATA_DIR", "ARTMIND_VAULT"):
        env.pop(key, None)
    env.update({k: v for k, v in env_overrides.items() if v is not None})
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, env=env, cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_paths_derive_from_the_discovered_vault(tmp_path):
    (tmp_path / ".artmind").mkdir()

    out = _probe(tmp_path)

    assert out["vault"] == str(tmp_path.resolve())
    assert out["home"] == str(tmp_path.resolve() / ".artmind")
    assert out["data"] == str(tmp_path.resolve() / ".artmind" / "data")
    assert out["schemas"] == str(tmp_path.resolve() / ".artmind" / "domains" / "schemas")
    assert out["kg"] == str(tmp_path.resolve() / ".artmind" / "data" / "kg")


def test_paths_follow_the_vault_from_a_subdirectory(tmp_path):
    (tmp_path / ".artmind").mkdir()
    deep = tmp_path / "notes" / "august"
    deep.mkdir(parents=True)

    assert _probe(deep)["vault"] == str(tmp_path.resolve())


def test_outside_a_vault_falls_back_to_the_legacy_layout(tmp_path):
    """The bridge that keeps the existing suite and existing installs working."""
    out = _probe(tmp_path)

    assert out["vault"] is None
    assert out["home"] == str(Path.home() / ".artmind")
    assert out["data"] == str(Path.home() / "artmind_data")


def test_artmind_home_still_overrides_everything(tmp_path):
    """conftest.py sets ARTMIND_HOME to a temp dir for the whole suite; that
    must keep working, vault or no vault."""
    (tmp_path / ".artmind").mkdir()
    override = tmp_path / "override"
    override.mkdir()

    out = _probe(tmp_path, ARTMIND_HOME=str(override))

    assert out["home"] == str(override.resolve())
