"""Config loading, most-specific-first (docs/vault.md, "Machine-level config").

Subprocesses again: paths.py loads config at import.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PROBE = (
    "import json, os, paths;"
    "print(json.dumps({"
    "'db': os.environ.get('ARTMIND_KG_NEO4J_DATABASE', ''),"
    "'model': os.environ.get('ARTMIND_KG_LLM_MODEL', ''),"
    "'loaded': [str(p) for p in paths.LOADED_ENV_FILES],"
    "}))"
)


def _probe(cwd: Path, home: Path, **extra) -> dict:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "HOME": str(home)}
    for key in ("ARTMIND_HOME", "ARTMIND_DATA_DIR", "ARTMIND_VAULT",
                "ARTMIND_KG_NEO4J_DATABASE", "ARTMIND_KG_LLM_MODEL",
                "ARTMIND_ALLOW_REPO_ENV"):
        env.pop(key, None)
    env.update(extra)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, env=env, cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _machine_config(home: Path, body: str) -> None:
    (home / ".artmind").mkdir(parents=True, exist_ok=True)
    (home / ".artmind" / "config.env").write_text(body)


def _make_vault(root: Path) -> Path:
    """A vault is a directory whose .artmind/ holds a vault.yaml manifest."""
    (root / ".artmind").mkdir(parents=True, exist_ok=True)
    (root / ".artmind" / "vault.yaml").write_text("ingest: {}\n")
    return root


def test_vault_config_overrides_the_machine_config(tmp_path):
    home = tmp_path / "home"
    _machine_config(home, "ARTMIND_KG_NEO4J_DATABASE=machine\nARTMIND_KG_LLM_MODEL=shared\n")
    vault_root = _make_vault(tmp_path / "MyVault")
    (vault_root / ".artmind" / "config.env").write_text("ARTMIND_KG_NEO4J_DATABASE=thisvault\n")

    out = _probe(vault_root, home)

    assert out["db"] == "thisvault", "the vault's own value must win"
    assert out["model"] == "shared", "machine identity still reaches the vault"


def test_machine_config_is_loaded_when_the_vault_has_none(tmp_path):
    home = tmp_path / "home"
    _machine_config(home, "ARTMIND_KG_LLM_MODEL=shared\n")
    vault_root = _make_vault(tmp_path / "MyVault")

    assert _probe(vault_root, home)["model"] == "shared"


def test_a_real_environment_variable_beats_both(tmp_path):
    home = tmp_path / "home"
    _machine_config(home, "ARTMIND_KG_NEO4J_DATABASE=machine\n")
    vault_root = _make_vault(tmp_path / "MyVault")
    (vault_root / ".artmind" / "config.env").write_text("ARTMIND_KG_NEO4J_DATABASE=thisvault\n")

    out = _probe(vault_root, home, ARTMIND_KG_NEO4J_DATABASE="fromenv")

    assert out["db"] == "fromenv"


def test_the_legacy_dot_env_still_loads(tmp_path):
    """Existing installs keep working: ~/.artmind/.env is still read."""
    home = tmp_path / "home"
    (home / ".artmind").mkdir(parents=True)
    (home / ".artmind" / ".env").write_text("ARTMIND_KG_LLM_MODEL=legacy\n")
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    assert _probe(outside, home)["model"] == "legacy"


def test_the_checkout_env_is_not_loaded_by_default(tmp_path):
    """Guardrail 1: it silently loaded another knowledge base's credentials and
    graph whenever a run folder had none of its own."""
    home = tmp_path / "home"
    (home / ".artmind").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    loaded = _probe(outside, home)["loaded"]

    assert not [p for p in loaded if p == str(REPO_ROOT / ".env")]


def test_the_checkout_env_can_be_opted_back_in(tmp_path):
    home = tmp_path / "home"
    (home / ".artmind").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    loaded = _probe(outside, home, ARTMIND_ALLOW_REPO_ENV="1")["loaded"]

    if (REPO_ROOT / ".env").is_file():
        assert str(REPO_ROOT / ".env") in loaded


def test_the_retired_vault_dir_variable_warns_instead_of_silently_doing_nothing(tmp_path):
    """ARTMIND_VAULT_DIR used to SELECT the vault; it is now an output of
    discovery. Existing .env files still set it, and a silent no-op is the worst
    shape for a config change."""
    home = tmp_path / "home"
    (home / ".artmind").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    outside.mkdir()

    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "HOME": str(home),
           "ARTMIND_VAULT_DIR": str(tmp_path / "somewhere")}
    for key in ("ARTMIND_HOME", "ARTMIND_DATA_DIR", "ARTMIND_VAULT"):
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-c", "import paths; print(paths.ARTMIND_VAULT_DIR)"],
        capture_output=True, text=True, env=env, cwd=outside,
    )

    assert result.returncode == 0, result.stderr
    assert "ARTMIND_VAULT_DIR is no longer read" in result.stderr
    assert result.stdout.strip() == "None", "it must not select the vault"
