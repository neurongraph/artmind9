"""ARTMIND_AGENT_CWD: the directory an agent process (claude-sdk, ACP) must
run in so it resolves `.claude/skills/` and `.opencode/agent/` correctly
(docs/vault.md, "ACP agent modes").

Regression coverage for the bug this constant fixes: the ACP backend used to
default its session `cwd` to `ARTMIND_HOME` unconditionally, which inside a
vault is `<vault>/.artmind/` -- one level below the vault root, where neither
directory is symlinked in. opencode then reported the `artmind` ACP mode as
"not found" even though `.opencode/agent/artmind.md` genuinely existed, just
one directory up from where the session's `cwd` pointed.

Subprocess-based, like test_webui_acp_sdk_free.py: `paths.py`'s constants are
computed once at import time from `ARTMIND_VAULT`/`ARTMIND_HOME`, so they must
be set before that first import rather than monkeypatched afterward.
"""
import os
import subprocess
import sys


def _agent_cwd(env: dict, cwd: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", "import paths; print(paths.ARTMIND_AGENT_CWD)"],
        capture_output=True, text=True, check=True, env=env, cwd=cwd,
    )
    return result.stdout.strip()


def test_agent_cwd_is_the_vault_root_inside_a_vault(tmp_path):
    """`ARTMIND_VAULT` (or the cwd walk-up) resolving a vault must win: the
    agent's cwd becomes the vault root, where `scaffold_vault` symlinks in
    both `.claude/skills/` and `.opencode/agent/`."""
    vault = tmp_path / "myvault"
    (vault / ".artmind").mkdir(parents=True)
    (vault / ".artmind" / "vault.yaml").write_text("ingest:\n  trigger: manual\n  mappings: []\n")

    env = dict(os.environ)
    env["ARTMIND_VAULT"] = str(vault)
    env.pop("ARTMIND_HOME", None)

    assert _agent_cwd(env, cwd=str(tmp_path)) == str(vault.resolve())


def test_agent_cwd_is_artmind_home_outside_a_vault(tmp_path):
    """No vault in play: the machine home is already where both directories
    live (scaffold_run_folder's non-vault path), so ARTMIND_AGENT_CWD must
    still equal ARTMIND_HOME -- unchanged behaviour for this case."""
    home = tmp_path / "home"

    env = dict(os.environ)
    env["ARTMIND_HOME"] = str(home)
    env.pop("ARTMIND_VAULT", None)

    assert _agent_cwd(env, cwd=str(tmp_path)) == str(home.resolve())
