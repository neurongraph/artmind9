"""Per-browser-tab agent sessions for the chat web UI."""

import time
from typing import Any, Callable

from claude_agent_sdk import ClaudeSDKClient

from artmind.webui.agent import agent_options

IDLE_TIMEOUT_S = 30 * 60


def _default_factory() -> ClaudeSDKClient:
    return ClaudeSDKClient(agent_options())


class SessionRegistry:
    """One lazily-connected SDK client per browser tab, reaped when idle."""

    def __init__(
        self,
        client_factory: Callable[[], Any] = _default_factory,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
    ) -> None:
        self._client_factory = client_factory
        self._idle_timeout_s = idle_timeout_s
        self._sessions: dict[str, tuple[Any, float]] = {}

    async def get(self, session_id: str) -> Any:
        entry = self._sessions.get(session_id)
        if entry is None:
            client = self._client_factory()
            await client.connect()
        else:
            client = entry[0]
        self._sessions[session_id] = (client, time.monotonic())
        return client

    def peek(self, session_id: str) -> Any | None:
        entry = self._sessions.get(session_id)
        return entry[0] if entry else None

    async def drop(self, session_id: str) -> None:
        entry = self._sessions.pop(session_id, None)
        if entry:
            await entry[0].disconnect()

    async def sweep(self) -> int:
        now = time.monotonic()
        stale = [
            sid
            for sid, (_, last_used) in self._sessions.items()
            if now - last_used > self._idle_timeout_s
        ]
        for sid in stale:
            await self.drop(sid)
        return len(stale)
