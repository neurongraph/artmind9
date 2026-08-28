"""Claude Agent SDK backend: composes the SDK client with the event mapper."""

from typing import Any, AsyncIterator

from claude_agent_sdk import ClaudeSDKClient

from artmind.webui.agent import EventMapper, agent_options
from artmind.webui.backends.base import UIEvent
from artmind.webui.profiles import AgentProfile, QA_PROFILE


def _message_session_id(message: Any) -> str | None:
    """Pull the SDK session id off a streamed message, if it carries one.

    Most message types (``ResultMessage``, ``SessionMessage``, ``UserMessage``)
    expose ``session_id`` directly; the ``SystemMessage`` init frame carries it
    under ``data['session_id']`` instead. We read both so the id is captured as
    early as the first turn.
    """
    sid = getattr(message, "session_id", None)
    if sid:
        return sid
    data = getattr(message, "data", None)
    if isinstance(data, dict):
        return data.get("session_id")
    return None


class ClaudeSDKBackend:
    def __init__(
        self,
        profile: AgentProfile = QA_PROFILE,
        resume: str | None = None,
        *,
        mcp_servers: dict[str, Any] | None = None,
        allowed_tools: list[str] | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        # Remember the profile + current resume target so ``restart`` can
        # rebuild an equivalent client (optionally resuming the same session).
        # ``mcp_servers``/``allowed_tools`` (both optional, default no-op) let a
        # front-end register in-process SDK tools; they're preserved across
        # ``restart`` so a resumed session keeps the same tool surface.
        # ``model``/``fallback_model``/``env`` (also optional, see
        # ``ARTMIND_SDK_MODEL``/``ARTMIND_SDK_BASE_URL`` in ``backends/__init__.py``)
        # are preserved the same way.
        self._profile = profile
        self._resume = resume
        self._mcp_servers = mcp_servers
        self._allowed_tools = allowed_tools
        self._model = model
        self._fallback_model = fallback_model
        self._env = env
        self._session_id: str | None = resume
        self._client = ClaudeSDKClient(
            agent_options(
                profile,
                resume=resume,
                mcp_servers=mcp_servers,
                allowed_tools=allowed_tools,
                model=model,
                fallback_model=fallback_model,
                env=env,
            )
        )

    async def connect(self) -> None:
        await self._client.connect()

    async def disconnect(self) -> None:
        await self._client.disconnect()

    async def query(self, prompt: str) -> None:
        await self._client.query(prompt)

    async def receive_events(self) -> AsyncIterator[UIEvent]:
        mapper = EventMapper()
        async for message in self._client.receive_response():
            # Capture the session id as it flows past so a later refresh can
            # resume this exact conversation (A7 / ADR 0007).
            sid = _message_session_id(message)
            if sid:
                self._session_id = sid
            for event in mapper.map(message):
                yield event

    async def interrupt(self) -> None:
        await self._client.interrupt()

    # ---- durable session lifecycle (ADR 0007 / A7) -----------------------

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def supports_resume(self) -> bool:
        return True

    async def restart(self, *, preserve_context: bool = True) -> None:
        """Rebuild the SDK client so a newly-authored skill is discovered.

        When ``preserve_context`` is set and a session id has been observed,
        the new client resumes it (``ClaudeAgentOptions.resume``) so the
        conversation continues seamlessly; otherwise it starts a clean session.
        """
        resume = self._session_id if (preserve_context and self._session_id) else None
        await self._client.disconnect()
        self._resume = resume
        self._client = ClaudeSDKClient(
            agent_options(
                self._profile,
                resume=resume,
                mcp_servers=self._mcp_servers,
                allowed_tools=self._allowed_tools,
                model=self._model,
                fallback_model=self._fallback_model,
                env=self._env,
            )
        )
        await self._client.connect()
