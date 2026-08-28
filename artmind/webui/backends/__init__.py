"""Backend selection for the chat web UI.

``claude-sdk`` runs the Claude Agent SDK (spawns the ``claude`` CLI),
configured via:

- ``ARTMIND_SDK_MODEL``: model for the ``claude`` CLI to use, e.g.
  ``claude-sonnet-5`` or ``claude-opus-5``. Unset leaves the CLI's own default
  model resolution in charge (your global ``claude`` config/login) — which can
  resolve to a model alias your account or endpoint doesn't actually have
  access to (surfaces as "Agent error: There's an issue with the selected
  model ... It may not exist or you may not have access to it"). Set this to
  pin a model explicitly instead of fighting that resolution. See
  ``claude-api`` for current model ids.
- ``ARTMIND_SDK_FALLBACK_MODEL``: model the SDK automatically retries with if
  ``ARTMIND_SDK_MODEL`` (or the CLI's default) is overloaded. Optional.
- ``ARTMIND_SDK_BASE_URL``: point the spawned ``claude`` CLI at a custom
  Anthropic-compatible endpoint (e.g. an enterprise gateway), independent of
  the KG pipeline's own ``ANTHROPIC_BASE_URL``/``ARTMIND_KG_LLM_URL`` (those
  feed a *different*, OpenAI-style HTTP client — see ``extraction.py``'s
  ``ibm_ica_client_env``). Two gotchas this exists to route around:

  1. The CLI always appends ``/v1/messages`` itself (matching
     ``api.anthropic.com``'s own convention: base = host only). If your
     gateway's KG-facing var already ends in ``/v1`` (typical for an
     OpenAI-style client, which appends ``/chat/completions``), the CLI-facing
     value here must have that trailing ``/v1`` **stripped** — e.g. KG uses
     ``https://gateway/ica/v1``, this var wants ``https://gateway/ica``.
     Getting this wrong 404s and the CLI reports it as "model may not exist or
     you may not have access to it" — misleading, always check
     ``claude --debug-file <path>`` for the real HTTP status when this var is
     set and the chat still fails.
  2. Setting this alone is not enough if you're logged into a
     ``claude.ai``/console subscription (``claude auth status``): the CLI
     prefers that OAuth session over ``ANTHROPIC_AUTH_TOKEN``/
     ``ANTHROPIC_API_KEY`` and silently ignores the token you set for the
     custom endpoint, which then rejects the CLI's own session credential.
     So whenever this var is set, ``create_backend`` also points the
     subprocess's ``CLAUDE_CONFIG_DIR`` at an isolated, credential-free
     directory under the run folder — this forces pure env-var API-key auth
     for *this* subprocess only, without touching your personal
     ``~/.claude`` login used elsewhere. ``ANTHROPIC_AUTH_TOKEN`` (or
     ``ANTHROPIC_API_KEY``) must still be set in the process env (as it
     already is for ``ARTMIND_KG_LLM_PROVIDER=ibm_ica``) — it's inherited by
     the subprocess unchanged, only the base URL and config dir are overridden.

  Leave unset to use the CLI's normal login (subscription or its own env-var
  auth) against ``api.anthropic.com``, untouched. Also settable per-run via
  ``artmind chat-ui``/``admin-ui --base-url``, which beats this var — pass
  ``--base-url ""`` to force the CLI's normal routing for one run even when
  the env var is set (the quick way to flip back to a subscription login
  without editing ``.env``).

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
- ``ARTMIND_ACP_MODEL``: model for the ACP agent to use, e.g.
  ``anthropic/claude-sonnet-4`` or ``ollama/gemma4:26b-mlx``. opencode-specific:
  passed via the ``OPENCODE_CONFIG_CONTENT`` env var (opencode's ``acp``
  subcommand has no ``--model`` flag, and ACP itself has no standard
  model-selection method). Unset leaves opencode's own config (global
  ``opencode.jsonc`` or project config) in charge; other ACP agents ignore it.
"""

import os
import shlex
from typing import Callable

from artmind.webui.backends.base import AgentBackend, UIEvent
from artmind.webui.profiles import ADMIN_PROFILE, QA_PROFILE, AgentProfile

BACKEND_NAMES = ("claude-sdk", "acp")
DEFAULT_BACKEND = "claude-sdk"

_acp_cmd_override: list[str] | None = None
_sdk_model_override: str | None = None
_sdk_base_url_override: str | None = None


def set_acp_agent_cmd(cmd: str | None) -> None:
    """CLI-flag override for the ACP agent command (beats the env var)."""
    global _acp_cmd_override
    _acp_cmd_override = shlex.split(cmd) if cmd else None


def set_sdk_model(model: str | None) -> None:
    """CLI-flag override for the claude-sdk backend's model (beats ``ARTMIND_SDK_MODEL``)."""
    global _sdk_model_override
    _sdk_model_override = model or None


def set_sdk_base_url(url: str | None) -> None:
    """CLI-flag override for the claude-sdk backend's base URL (beats
    ``ARTMIND_SDK_BASE_URL``). Unlike ``set_sdk_model``, ``""`` is a
    meaningful value here (not normalized to ``None``): it explicitly forces
    the CLI's normal routing (OAuth login or its own env-var auth against
    ``api.anthropic.com``) for one run even when ``ARTMIND_SDK_BASE_URL`` is
    set in ``.env`` — the quick way to flip back to a subscription login
    without editing the file. ``None`` (the flag not passed) means "defer to
    the env var", the actual default.
    """
    global _sdk_base_url_override
    _sdk_base_url_override = url


def _sdk_env_overrides() -> dict[str, str]:
    """Env vars to merge onto the spawned ``claude`` CLI when a custom
    endpoint is in play — the ``--base-url`` flag if passed (including an
    explicit empty string, which forces the default off), else
    ``ARTMIND_SDK_BASE_URL`` (see module docstring). Isolating
    ``CLAUDE_CONFIG_DIR`` only happens when a URL actually applies, so a
    default setup never touches the operator's own ``~/.claude`` login.
    """
    base_url = (
        _sdk_base_url_override
        if _sdk_base_url_override is not None
        else os.environ.get("ARTMIND_SDK_BASE_URL")
    )
    if not base_url:
        return {}
    from paths import ARTMIND_HOME

    return {
        "ANTHROPIC_BASE_URL": base_url,
        "CLAUDE_CONFIG_DIR": str(ARTMIND_HOME / ".claude-sdk-auth"),
    }


def create_backend(
    name: str,
    profile: AgentProfile = QA_PROFILE,
    *,
    mcp_servers: dict | None = None,
    allowed_tools: list[str] | None = None,
) -> AgentBackend:
    """Build a backend for ``name`` wearing ``profile``'s persona + skills.

    Both backends are profile-agnostic transport; the profile supplies the
    skill scoping and system prompt (claude-sdk) or the ACP mode + preamble
    (acp). ``ARTMIND_ACP_MODE`` still overrides the profile's mode when set.

    ``mcp_servers`` / ``allowed_tools`` (both optional, default no-op) let a
    front-end register in-process SDK tools. Only the ``claude-sdk`` backend
    supports them — ACP has no in-process tool mechanism (it passes an empty
    ``mcpServers`` at ``session/new`` and supports only external MCP servers),
    so the params are ignored there. See the canvas ``show_card`` tool.
    """
    if name == "claude-sdk":
        from artmind.webui.backends.claude_sdk import ClaudeSDKBackend

        return ClaudeSDKBackend(
            profile,
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            model=_sdk_model_override or os.environ.get("ARTMIND_SDK_MODEL") or None,
            fallback_model=os.environ.get("ARTMIND_SDK_FALLBACK_MODEL") or None,
            env=_sdk_env_overrides() or None,
        )
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
            model=os.environ.get("ARTMIND_ACP_MODEL") or None,
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
    "set_sdk_model",
    "set_sdk_base_url",
]
