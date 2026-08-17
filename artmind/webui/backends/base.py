"""Backend-neutral contract between the chat web UI and agent implementations.

A backend owns one conversation (one browser tab): it holds the agent
process/connection, accepts one prompt at a time, and yields the neutral UI
event dicts that ``static/app.js`` renders (``text_delta``, ``thinking_delta``,
``block_done``, ``tool_call``, ``tool_result``, ``turn_done``, ``error``).

Dependency-free (stdlib only): both the Claude SDK path (``agent.py``) and the
ACP path (``acp_events.py``) import from here, so the ACP path never has to
pull in ``claude_agent_sdk`` just to clip trace text.
"""

import json
from typing import Any, AsyncIterator, Protocol

UIEvent = dict[str, Any]

TRACE_CLIP = 600


def clip(value: Any, limit: int = TRACE_CLIP) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else f"{text[:limit]} … [{len(text)} chars total]"


class AgentBackend(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def query(self, prompt: str) -> None: ...

    def receive_events(self) -> AsyncIterator[UIEvent]:
        """Yield UI events for the in-flight turn; must always terminate."""
        ...

    async def interrupt(self) -> None: ...

    # ---- durable session lifecycle (ADR 0007 / A7) -----------------------

    @property
    def session_id(self) -> str | None:
        """The provider's session id for this conversation, or ``None`` until
        one has been observed. On the Claude SDK path this is what
        ``restart(preserve_context=True)`` resumes; on the ACP path it is
        informational only (ACP resume is unverified — see ``supports_resume``).
        """
        ...

    @property
    def supports_resume(self) -> bool:
        """Whether ``restart(preserve_context=True)`` actually preserves the
        conversation. True for the Claude SDK backend (SDK ``resume``); False
        for ACP, where a refresh is a clean restart that drops the thread
        (ADR 0007 explicitly okays losing context there)."""
        ...

    async def restart(self, *, preserve_context: bool = True) -> None:
        """Tear down and rebuild the underlying agent connection.

        The reason this exists: skills are read only at agent-session start
        (no hot-reload), so authoring a ``SKILL.md`` mid-conversation and using
        it immediately requires refreshing the session. When
        ``preserve_context`` is set and the backend ``supports_resume``, the
        rebuilt session resumes the prior conversation so the thread survives
        the refresh; otherwise it starts fresh.
        """
        ...
