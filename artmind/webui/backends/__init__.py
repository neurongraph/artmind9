"""Backend selection for the chat web UI.

``claude-sdk`` runs the Claude Agent SDK (spawns the ``claude`` CLI).
``acp`` spawns an Agent Client Protocol agent subprocess — any ACP agent
works, configured via:

- ``ARTMIND_ACP_AGENT_CMD``: agent command line (default ``opencode acp``)
- ``ARTMIND_ACP_CWD``: session working directory (default: project root, so
  the agent discovers the artmind skills in ``.claude/skills/``)
- ``ARTMIND_ACP_PROMPT_PREAMBLE``: set to ``1`` to prepend the artmind persona
  to the first prompt of each session (fallback when the agent has no other
  way to receive a system prompt)
- ``ARTMIND_ACP_MODE``: ACP session mode to select (default ``artmind`` — the
  opencode agent defined in ``.opencode/agent/artmind.md``; unknown modes are
  ignored with a warning, so other ACP agents still work). Set to an empty
  string to skip mode selection.
"""

import os
import shlex

from artmind.webui.backends.base import AgentBackend, UIEvent

BACKEND_NAMES = ("claude-sdk", "acp")
DEFAULT_BACKEND = "claude-sdk"

_acp_cmd_override: list[str] | None = None


def set_acp_agent_cmd(cmd: str | None) -> None:
    """CLI-flag override for the ACP agent command (beats the env var)."""
    global _acp_cmd_override
    _acp_cmd_override = shlex.split(cmd) if cmd else None


def create_backend(name: str) -> AgentBackend:
    if name == "claude-sdk":
        from artmind.webui.backends.claude_sdk import ClaudeSDKBackend

        return ClaudeSDKBackend()
    if name == "acp":
        from artmind.webui.agent import PROJECT_ROOT
        from artmind.webui.backends.acp import ACPBackend

        agent_cmd = _acp_cmd_override or shlex.split(
            os.environ.get("ARTMIND_ACP_AGENT_CMD", "opencode acp")
        )
        return ACPBackend(
            agent_cmd=agent_cmd,
            cwd=os.environ.get("ARTMIND_ACP_CWD", str(PROJECT_ROOT)),
            prompt_preamble=os.environ.get("ARTMIND_ACP_PROMPT_PREAMBLE") == "1",
            mode=os.environ.get("ARTMIND_ACP_MODE", "artmind") or None,
        )
    raise ValueError(f"unknown chat backend: {name!r}")


__all__ = [
    "AgentBackend",
    "UIEvent",
    "BACKEND_NAMES",
    "DEFAULT_BACKEND",
    "create_backend",
    "set_acp_agent_cmd",
]
