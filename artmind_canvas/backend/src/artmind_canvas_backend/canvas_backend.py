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

import asyncio
import logging
from typing import Any, AsyncIterator, Callable

from claude_agent_sdk import create_sdk_mcp_server

from artmind.webui.backends import AgentBackend, UIEvent, create_backend

from artmind_canvas_backend.profiles import CANVAS_PROFILE
from artmind_canvas_backend.render_events import (
    document_card,
    graph_view_card,
    micro_ui_card,
    placement_card,
    provenance_card,
    render_event,
)
from artmind_canvas_backend.show_card_tool import (
    QUALIFIED_TOOL_NAME,
    make_show_card_tool,
)

logger = logging.getLogger(__name__)

# Canned test hooks (offline, no serve/auth) and their real user-facing aliases.
# Both resolve live data — the card spec carries real selectors; only the
# surrounding narration is canned. The test hooks back ``test_render.py``; the
# real commands (Phase C) are what users type. A first-token match dispatches
# both, so ``/graph`` never shadows ``/graph-test``.
_RENDER_TEST = "/render-test"
_PROV_TEST = "/prov-test"
_MICROUI_TEST = "/microui-test"
_PLACEMENT_TEST = "/placement-test"
_GRAPH_TEST = "/graph-test"
_DOCUMENT = "/document"
_PROVENANCE = "/provenance"
_PLACEMENT = "/placement"
_GRAPH = "/graph"


class CanvasBackend:
    """Neutral-contract backend that additionally emits ``render`` events."""

    def __init__(self, inner_factory: Callable[..., AgentBackend]) -> None:
        # ``inner_factory`` is called with ``mcp_servers`` / ``allowed_tools``
        # kwargs when the inner backend is finally built (first real prompt), so
        # the ``show_card`` tool is wired into the SDK session. It is never
        # called for canned/slash hooks, which short-circuit below.
        self._inner_factory = inner_factory
        self._inner: AgentBackend | None = None
        self._canned: list[UIEvent] | None = None
        # Render side-channel: the ``show_card`` tool enqueues ``render`` events
        # here (its return value goes to the model, not the SSE stream), and
        # ``receive_events`` drains them into the neutral stream. Created once so
        # the tool closure and the drain share one queue across turns; each turn
        # fully drains it (end-of-loop drain), so it is empty between turns.
        self._render_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

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
            server = create_sdk_mcp_server(
                "canvas", tools=[make_show_card_tool(self._render_queue)]
            )
            inner = self._inner_factory(
                mcp_servers={"canvas": server},
                allowed_tools=[QUALIFIED_TOOL_NAME],
            )
            await inner.connect()
            self._inner = inner
        return self._inner

    async def query(self, prompt: str) -> None:
        stripped = prompt.strip()
        cmd, _, rest = stripped.partition(" ")
        canned = self._canned_sequence(cmd.lower(), rest.strip())
        if canned is not None:
            self._canned = canned
            return
        self._canned = None
        inner = await self._ensure_inner()
        await inner.query(prompt)

    @classmethod
    def _canned_sequence(cls, cmd: str, arg: str) -> list[UIEvent] | None:
        """Map a leading slash-command token to a canned render sequence.

        Real user commands and their ``-test`` aliases share a builder — the
        card spec is identical; only the test hooks exist to be driven offline.
        Returns ``None`` for anything that is not a canvas slash-command, so the
        prompt falls through to the inner agent.
        """
        if cmd in (_RENDER_TEST, _DOCUMENT):
            return cls._render_test_sequence(arg)
        if cmd in (_PROV_TEST, _PROVENANCE):
            return cls._prov_test_sequence(arg)
        if cmd in (_PLACEMENT_TEST, _PLACEMENT):
            return cls._placement_test_sequence(arg)
        if cmd in (_GRAPH_TEST, _GRAPH):
            return cls._graph_test_sequence(arg)
        if cmd == _MICROUI_TEST:
            return cls._microui_test_sequence(arg)
        return None

    async def receive_events(self) -> AsyncIterator[UIEvent]:
        if self._canned is not None:
            for event in self._canned:
                yield event
            self._canned = None
            return
        assert self._inner is not None  # query() connected it
        async for event in self._inner.receive_events():
            yield event
            # Surface any render events the ``show_card`` tool enqueued while the
            # SDK processed this event — the Card lands mid-answer, around the
            # tool_call/tool_result trace.
            for render in self._drain_render_queue():
                yield render
        # End-of-loop drain: guarantees a late tool execution's Card lands even
        # if the SDK deferred it past the final message (before turn_done here).
        for render in self._drain_render_queue():
            yield render

    def _drain_render_queue(self) -> list[UIEvent]:
        drained: list[UIEvent] = []
        while not self._render_queue.empty():
            drained.append(self._render_queue.get_nowait())
        return drained

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

    @staticmethod
    def _prov_test_sequence(arg: str) -> list[UIEvent]:
        """Canned turn spawning a ``provenance`` Card: ``/prov-test <domain> <reference>``."""
        domain, _, reference = arg.partition(" ")
        domain = domain or "default"
        reference = reference.strip() or "artmind"
        return [
            {"type": "text_delta", "text": f"Tracing provenance for `{reference}` in `{domain}`…"},
            {"type": "block_done", "block": "text"},
            render_event(provenance_card([domain], reference=reference)),
            {"type": "turn_done", "turns": 1, "duration_s": 0.0, "cost": None},
        ]

    @staticmethod
    def _placement_test_sequence(arg: str) -> list[UIEvent]:
        """Canned turn spawning a ``placement`` Card: ``/placement-test <vaultPath> [domain...]``.

        The Card itself fetches the live proposal from ``/api/placement/propose``;
        this hook only proves the render wire spawns the Card offline.
        """
        parts = arg.split()
        vault_path = parts[0] if parts else "README.md"
        domains = parts[1:]
        return [
            {"type": "text_delta", "text": f"Proposing placement for `{vault_path}`…"},
            {"type": "block_done", "block": "text"},
            render_event(placement_card(vault_path, domains=domains or None)),
            {"type": "turn_done", "turns": 1, "duration_s": 0.0, "cost": None},
        ]

    @staticmethod
    def _graph_test_sequence(arg: str) -> list[UIEvent]:
        """Canned turn spawning a ``graph-view`` Card: ``/graph-test <domain> <reference>``.

        The Card fetches the live subgraph from ``/api/graph``; this hook only
        proves the render wire spawns the Card offline (no serve/Neo4j needed).
        """
        domain, _, reference = arg.partition(" ")
        domain = domain or "default"
        reference = reference.strip() or "artmind"
        return [
            {"type": "text_delta", "text": f"Mapping the neighbourhood of `{reference}` in `{domain}`…"},
            {"type": "block_done", "block": "text"},
            render_event(graph_view_card([domain], reference=reference)),
            {"type": "turn_done", "turns": 1, "duration_s": 0.0, "cost": None},
        ]

    @staticmethod
    def _microui_test_sequence(arg: str) -> list[UIEvent]:
        """Canned turn spawning a ``micro-ui`` Card: sandboxed agent-authored HTML.

        ``arg`` is an optional title; the demo widget is a self-contained counter
        proving scripts run inside the sandbox with no app access.
        """
        title = arg.strip() or "demo widget"
        html = (
            "<!doctype html><meta charset=utf-8>"
            "<style>body{font:14px -apple-system,sans-serif;color:#e6e8ec;margin:0;"
            "padding:14px;background:#0b0d11}button{font:inherit;color:#fff;"
            "background:#5b8def;border:0;border-radius:6px;padding:6px 12px;"
            "cursor:pointer}b{font-size:22px}</style>"
            "<p>Sandboxed micro-UI. Count: <b id=n>0</b></p>"
            "<button id=b>+1</button>"
            "<script>let n=0,el=document.getElementById('n');"
            "document.getElementById('b').onclick=()=>{el.textContent=++n};</script>"
        )
        return [
            {"type": "text_delta", "text": f"Rendering a sandboxed micro-UI (`{title}`)…"},
            {"type": "block_done", "block": "text"},
            render_event(micro_ui_card(html, title=title)),
            {"type": "turn_done", "turns": 1, "duration_s": 0.0, "cost": None},
        ]


def canvas_backend_factory(name: str) -> AgentBackend:
    """``SessionRegistry`` client_factory: a CanvasBackend over artmind's backend.

    The inner factory forwards the canvas ``show_card`` MCP server (built per
    backend in ``_ensure_inner``) to ``create_backend`` — the ``claude-sdk``
    branch wires it into the SDK session; ``acp`` ignores it (Phase C: ACP has
    no in-process tool mechanism and gets the slash-commands instead).
    """
    return CanvasBackend(
        lambda **kwargs: create_backend(name, CANVAS_PROFILE, **kwargs)
    )
