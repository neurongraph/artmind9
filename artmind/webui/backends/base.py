"""Backend-neutral contract between the chat web UI and agent implementations.

A backend owns one conversation (one browser tab): it holds the agent
process/connection, accepts one prompt at a time, and yields the neutral UI
event dicts that ``static/app.js`` renders (``text_delta``, ``thinking_delta``,
``block_done``, ``tool_call``, ``tool_result``, ``turn_done``, ``error``).
"""

from typing import Any, AsyncIterator, Protocol

UIEvent = dict[str, Any]


class AgentBackend(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def query(self, prompt: str) -> None: ...

    def receive_events(self) -> AsyncIterator[UIEvent]:
        """Yield UI events for the in-flight turn; must always terminate."""
        ...

    async def interrupt(self) -> None: ...
