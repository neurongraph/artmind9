"""Per-browser-tab agent sessions for the chat web UI."""

import asyncio
import time
from typing import Any, Callable

from claude_agent_sdk import ClaudeSDKClient

from artmind.webui.agent import agent_options

IDLE_TIMEOUT_S = 30 * 60


def _default_factory() -> ClaudeSDKClient:
    return ClaudeSDKClient(agent_options())


class SessionRegistry:
    """One lazily-connected SDK client per browser tab, reaped when idle.

    ``get``, ``drop``, and ``sweep`` all serialize on a single lock so that
    the check-then-act sequences they perform (look up an entry, then
    connect/disconnect it) can't interleave with each other. Without this,
    concurrent calls for the same session id can leak connections or evict a
    session a caller just started using.
    """

    def __init__(
        self,
        client_factory: Callable[[], Any] = _default_factory,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
    ) -> None:
        self._client_factory = client_factory
        self._idle_timeout_s = idle_timeout_s
        self._sessions: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, session_id: str) -> Any:
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                client = entry[0]
                self._sessions[session_id] = (client, time.monotonic())
                return client

            client = self._client_factory()
            try:
                await client.connect()
            except BaseException:
                disconnect = getattr(client, "disconnect", None)
                if disconnect is not None:
                    try:
                        await disconnect()
                    except Exception:
                        pass
                raise
            self._sessions[session_id] = (client, time.monotonic())
            return client

    def peek(self, session_id: str) -> Any | None:
        entry = self._sessions.get(session_id)
        return entry[0] if entry else None

    async def drop(self, session_id: str) -> None:
        async with self._lock:
            entry = self._sessions.pop(session_id, None)
            if entry:
                await entry[0].disconnect()

    async def sweep(self) -> int:
        async with self._lock:
            now = time.monotonic()
            stale_ids = [
                sid
                for sid, (_, last_used) in self._sessions.items()
                if now - last_used > self._idle_timeout_s
            ]
            dropped = 0
            for sid in stale_ids:
                entry = self._sessions.pop(sid, None)
                if entry:
                    await entry[0].disconnect()
                    dropped += 1
            return dropped
