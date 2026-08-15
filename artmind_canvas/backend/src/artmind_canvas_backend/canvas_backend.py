"""CanvasBackend — wraps a neutral artmind ``AgentBackend`` and owns the
canvas's richer contract (the ``render`` event, ADR 0014).

Phase 0 scope:
- Pass the 7 trace events (text_delta … turn_done, error) through unchanged.
- Emit a ``render`` event from a deterministic ``/render-test`` hook, proving the
  render wire end-to-end WITHOUT touching the two harness mappers (that is Phase
  5: the SDK ``EventMapper`` and ACP ``ACPEventMapper`` funnel through here).

The inner backend is built lazily on the first *real* prompt, so ``/render-test``
exercises the render path with no agent process and no auth — the render wire
stays verifiable offline.
"""

import logging
from typing import AsyncIterator, Callable

from artmind.webui.backends import AgentBackend, UIEvent, create_backend

from artmind_canvas_backend.profiles import CANVAS_PROFILE
from artmind_canvas_backend.render_events import document_card, render_event

logger = logging.getLogger(__name__)

_RENDER_TEST = "/render-test"


class CanvasBackend:
    """Neutral-contract backend that additionally emits ``render`` events."""

    def __init__(self, inner_factory: Callable[[], AgentBackend]) -> None:
        self._inner_factory = inner_factory
        self._inner: AgentBackend | None = None
        self._test_arg: str | None = None

    async def connect(self) -> None:
        # Lazy: defer the inner backend's connect (which spawns the agent
        # process) until the first real prompt. See module docstring.
        return None

    async def disconnect(self) -> None:
        if self._inner is not None:
            await self._inner.disconnect()
            self._inner = None

    async def _ensure_inner(self) -> AgentBackend:
        if self._inner is None:
            inner = self._inner_factory()
            await inner.connect()
            self._inner = inner
        return self._inner

    async def query(self, prompt: str) -> None:
        stripped = prompt.strip()
        if stripped.startswith(_RENDER_TEST):
            self._test_arg = stripped[len(_RENDER_TEST):].strip()
            return
        self._test_arg = None
        inner = await self._ensure_inner()
        await inner.query(prompt)

    async def receive_events(self) -> AsyncIterator[UIEvent]:
        if self._test_arg is not None:
            for event in self._render_test_sequence(self._test_arg):
                yield event
            self._test_arg = None
            return
        assert self._inner is not None  # query() connected it
        async for event in self._inner.receive_events():
            yield event

    async def interrupt(self) -> None:
        if self._inner is not None:
            await self._inner.interrupt()

    @staticmethod
    def _render_test_sequence(arg: str) -> list[UIEvent]:
        """Canned turn: some text, then a ``document`` render, then turn_done."""
        vault_path = arg or "README.md"
        return [
            {"type": "text_delta", "text": f"Opening `{vault_path}` on the canvas…"},
            {"type": "block_done", "block": "text"},
            render_event(document_card(vault_path)),
            {"type": "turn_done", "turns": 1, "duration_s": 0.0, "cost": None},
        ]


def canvas_backend_factory(name: str) -> AgentBackend:
    """``SessionRegistry`` client_factory: a CanvasBackend over artmind's backend."""
    return CanvasBackend(lambda: create_backend(name, CANVAS_PROFILE))
