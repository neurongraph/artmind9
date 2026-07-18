"""Backend selection for the chat web UI.

``claude-sdk`` runs the Claude Agent SDK (spawns the ``claude`` CLI).
``acp`` spawns an Agent Client Protocol agent subprocess — any ACP agent
works, configured via:

- ``ARTMIND_ACP_AGENT_CMD``: agent command line (default ``opencode acp``)
- ``ARTMIND_ACP_CWD``: session working directory (default: the run folder
  ``$ARTMIND_HOME``, so the agent discovers the artmind skills in
  ``.claude/skills/`` without exposing the source tree or corpus)
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
from typing import Callable

from artmind.webui.backends.base import AgentBackend, UIEvent
from artmind.webui.profiles import ADMIN_PROFILE, QA_PROFILE, AgentProfile

BACKEND_NAMES = ("claude-sdk", "acp")
DEFAULT_BACKEND = "claude-sdk"

_acp_cmd_override: list[str] | None = None


def set_acp_agent_cmd(cmd: str | None) -> None:
    """CLI-flag override for the ACP agent command (beats the env var)."""
    global _acp_cmd_override
    _acp_cmd_override = shlex.split(cmd) if cmd else None


def create_backend(name: str, profile: AgentProfile = QA_PROFILE) -> AgentBackend:
    """Build a backend for ``name`` wearing ``profile``'s persona + skills.

    Both backends are profile-agnostic transport; the profile supplies the
    skill scoping and system prompt (claude-sdk) or the ACP mode + preamble
    (acp). ``ARTMIND_ACP_MODE`` still overrides the profile's mode when set.
    """
    if name == "claude-sdk":
        from artmind.webui.backends.claude_sdk import ClaudeSDKBackend

        return ClaudeSDKBackend(profile)
    if name == "acp":
        from artmind.webui.backends.acp import ACPBackend
        from paths import ARTMIND_HOME

        agent_cmd = _acp_cmd_override or shlex.split(
            os.environ.get("ARTMIND_ACP_AGENT_CMD", "opencode acp")
        )
        return ACPBackend(
            agent_cmd=agent_cmd,
            cwd=os.environ.get("ARTMIND_ACP_CWD", str(ARTMIND_HOME)),
            prompt_preamble=os.environ.get("ARTMIND_ACP_PROMPT_PREAMBLE") == "1",
            mode=os.environ.get("ARTMIND_ACP_MODE", profile.acp_mode) or None,
            preamble_text=profile.system_append,
        )
    raise ValueError(f"unknown chat backend: {name!r}")


def backend_factory(profile: AgentProfile = QA_PROFILE) -> Callable[[str], AgentBackend]:
    """A ``SessionRegistry`` client_factory bound to ``profile``.

    Lets each front-end app pick its persona once — ``create_app`` and the
    registry stay profile-agnostic:

        create_app(SessionRegistry(client_factory=backend_factory(ADMIN_PROFILE)))
    """
    return lambda name: create_backend(name, profile)


__all__ = [
    "AgentBackend",
    "UIEvent",
    "BACKEND_NAMES",
    "DEFAULT_BACKEND",
    "AgentProfile",
    "QA_PROFILE",
    "ADMIN_PROFILE",
    "create_backend",
    "backend_factory",
    "set_acp_agent_cmd",
]
