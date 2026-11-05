"""Claude Agent SDK backend: composes the SDK client with the event mapper."""

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

from claude_agent_sdk import (
    ClaudeSDKClient,
    ResultMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    TaskUpdatedMessage,
)
from claude_agent_sdk.types import TERMINAL_TASK_STATUSES

from artmind.webui.agent import EventMapper, agent_options
from artmind.webui.backends.base import UIEvent
from artmind.webui.profiles import AgentProfile, QA_PROFILE
from artmind.webui.subagent_trace import SubagentTraceBinder


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


# A background sub-agent makes the agent end its turn early ("still running")
# and write a follow-up turn once the sub-agents finish. The stream is kept open
# for that follow-up, bounded so a lost completion message cannot hold the
# conversation (and its 409 claim) forever. Module attributes so tests can
# shorten them.
TASK_IDLE_TIMEOUT_S = 600.0  # nothing received while tasks are still running
TASK_FOLLOWUP_GRACE_S = 60.0  # all tasks done, but no follow-up result yet


def _task_ended(message: Any) -> str | None:
    """The task id a terminal task message closes, else None."""
    if isinstance(message, TaskNotificationMessage):
        return message.task_id
    if isinstance(message, TaskUpdatedMessage):
        status = message.status or (message.patch or {}).get("status")
        if status in TERMINAL_TASK_STATUSES:
            return message.task_id
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
        trace_context: Path | None = None,
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
        self._trace_context = trace_context
        # Created once: a restart rebuilds the client but keeps the bindings.
        self._trace_binder = SubagentTraceBinder(trace_context) if trace_context else None
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
                trace_binder=self._trace_binder,
            )
        )

    @property
    def trace_context_path(self) -> Path | None:
        return self._trace_context

    async def connect(self) -> None:
        await self._client.connect()

    async def disconnect(self) -> None:
        await self._client.disconnect()
        if self._trace_context is not None:
            self._trace_context.unlink(missing_ok=True)

    async def query(self, prompt: str) -> None:
        await self._client.query(prompt)

    async def receive_events(self) -> AsyncIterator[UIEvent]:
        mapper = EventMapper(capture=self._trace_context is not None)
        # Background tasks still running; while any are, a ResultMessage is not
        # the end of the turn: the agent will write a follow-up when they finish.
        pending: set[str] = set()
        held: list[UIEvent] = []  # the turn_done of a result that was not final
        messages = self._client.receive_messages().__aiter__()
        try:
            while True:
                timeout = None
                if pending:
                    timeout = TASK_IDLE_TIMEOUT_S
                elif held:
                    timeout = TASK_FOLLOWUP_GRACE_S
                try:
                    async with asyncio.timeout(timeout):
                        message = await messages.__anext__()
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    # Give up waiting and end the turn as it would have ended
                    # without this wait; late output is read by the next turn.
                    break
                # Capture the session id as it flows past so a later refresh can
                # resume this exact conversation (A7 / ADR 0007).
                sid = _message_session_id(message)
                if sid:
                    self._session_id = sid
                if isinstance(message, TaskStartedMessage):
                    pending.add(message.task_id)
                else:
                    ended = _task_ended(message)
                    if ended is not None:
                        pending.discard(ended)
                events = mapper.map(message)
                if isinstance(message, ResultMessage):
                    if pending:
                        held = events  # not final: the follow-up result replaces it
                        continue
                    held = []
                    for event in events:
                        yield event
                    return
                for event in events:
                    yield event
            for event in held:
                yield event
        finally:
            await messages.aclose()

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
        # Deliberately disconnects the inner client, not self.disconnect(), so the
        # session's trace-context file (spec 2026-10-11 §5.1) survives a refresh.
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
                trace_binder=self._trace_binder,
            )
        )
        await self._client.connect()
