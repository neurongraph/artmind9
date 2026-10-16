"""Per-conversation live agent sessions for the chat web UI."""

import asyncio
import time
from typing import Any, Callable

from artmind.webui.backends import DEFAULT_BACKEND, create_backend

IDLE_TIMEOUT_S = 30 * 60


class SessionRegistry:
    """One lazily-connected agent backend per conversation, reaped when idle.

    ``get``, ``drop``, and ``sweep`` all serialize on a single lock so that
    the check-then-act sequences they perform (look up an entry, then
    connect/disconnect it) can't interleave with each other. Without this,
    concurrent calls for the same session id can leak connections or evict a
    session a caller just started using.
    """

    def __init__(
        self,
        client_factory: Callable[[str], Any] = create_backend,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
    ) -> None:
        self._client_factory = client_factory
        self._idle_timeout_s = idle_timeout_s
        self._sessions: dict[str, tuple[Any, str, float]] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str, backend: str = DEFAULT_BACKEND) -> tuple[Any, bool]:
        """The live client for ``session_id`` and whether it is *fresh*.

        ``fresh`` is True when this call created the client -- including when it
        replaced one whose backend differed -- i.e. whenever the agent has no
        memory of the conversation. The chat route uses it to decide whether to
        replay a stored transcript (spec 2026-10-16 §4.1).
        """
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is not None:
                client, entry_backend, _ = entry
                if entry_backend == backend:
                    self._sessions[session_id] = (client, backend, time.monotonic())
                    return client, False
                # A backend switch inside one conversation: replace the session
                # rather than answering with the wrong agent. The caller replays
                # the stored transcript into the new one.
                self._sessions.pop(session_id)
                await client.disconnect()

            client = self._client_factory(backend)
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
            self._sessions[session_id] = (client, backend, time.monotonic())
            return client, True

    async def get(self, session_id: str, backend: str = DEFAULT_BACKEND) -> Any:
        client, _ = await self.get_or_create(session_id, backend)
        return client

    def peek(self, session_id: str) -> Any | None:
        entry = self._sessions.get(session_id)
        return entry[0] if entry else None

    async def refresh(self, session_id: str, *, preserve_context: bool = True) -> Any | None:
        """Refresh a live session's agent connection in place (A7 / ADR 0007).

        Drives ``backend.restart`` under the same lock as ``get``/``drop`` so a
        refresh can't interleave with a connect/evict for the same id. The
        client object is kept — it rebuilds its own inner connection — so the
        registry entry and its idle timer simply carry on. Used after a skill
        is authored mid-session: the rebuilt session discovers the new
        ``SKILL.md`` (skills are read only at session start), and on a
        resume-capable backend the conversation context survives.

        Returns the (same) client, or ``None`` if the session is not live —
        there is nothing to refresh for a session that was never started.
        """
        async with self._lock:
            entry = self._sessions.get(session_id)
            if entry is None:
                return None
            client, backend, _ = entry
            await client.restart(preserve_context=preserve_context)
            self._sessions[session_id] = (client, backend, time.monotonic())
            return client

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
                for sid, (_, _, last_used) in self._sessions.items()
                if now - last_used > self._idle_timeout_s
            ]
            dropped = 0
            for sid in stale_ids:
                entry = self._sessions.pop(sid, None)
                if entry:
                    await entry[0].disconnect()
                    dropped += 1
            return dropped
