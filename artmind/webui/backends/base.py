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
