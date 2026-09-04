"""load_env() must reflect the merged config, not one file.

paths.py loads the vault's config.env, the legacy .env, and the machine-wide
config.env, each with override=False -- so os.environ is already the merged
view. load_env() previously returned only the first file's values, which under
the machine/vault split (docs/vault.md) silently hid the machine's model
settings from ~30 call sites that read them from its return value.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_PROBE = (
    "import json, os;"
    "from utils.functions import load_env, resolve_llm_model;"
    "env = load_env();"
    "print(json.dumps({"
    "'model': env.get('ARTMIND_KG_LLM_MODEL'),"
    "'db': env.get('ARTMIND_KG_NEO4J_DATABASE'),"
    "'resolved': resolve_llm_model(env),"
    "}))"
)


def _probe(cwd: Path, home: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "HOME": str(home)}
    for key in ("ARTMIND_HOME", "ARTMIND_DATA_DIR", "ARTMIND_VAULT",
                "ARTMIND_KG_NEO4J_DATABASE", "ARTMIND_KG_LLM_MODEL",
                "ARTMIND_KG_LLM_PROVIDER", "ARTMIND_ALLOW_REPO_ENV"):
        env.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, env=env, cwd=cwd,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_machine_models_reach_a_vault_that_only_configures_its_graph(tmp_path):
    home = tmp_path / "home"
    (home / ".artmind").mkdir(parents=True)
    (home / ".artmind" / "config.env").write_text(
        "ARTMIND_KG_LLM_PROVIDER=ollama\nARTMIND_KG_LLM_MODEL=machine-model\n"
    )
    vault_root = tmp_path / "MyVault"
    (vault_root / ".artmind").mkdir(parents=True)
    (vault_root / ".artmind" / "vault.yaml").write_text("ingest: {}\n")
    (vault_root / ".artmind" / "config.env").write_text(
        "ARTMIND_KG_NEO4J_DATABASE=thisvault\n"
    )

    out = _probe(vault_root, home)

    assert out["db"] == "thisvault", "the vault's own value must still be visible"
    assert out["model"] == "machine-model", "machine identity must reach the vault"
    assert out["resolved"] == "machine-model", (
        "resolve_llm_model must not fall back to its hardcoded default"
    )
