"""The ``show_card`` agent tool (Phase C, ADR 0014).

Drives the tool handler directly (no SDK, no auth): feed args per ``cardType``,
assert the event enqueued onto the render queue equals ``render_event`` of the
matching ``render_events`` builder, and that bad args return an error to the
model without enqueuing anything. Also covers the ``CanvasBackend`` seam: a fake
inner backend that yields ``tool_call``/``tool_result`` while the handler
enqueues a render → ``receive_events`` interleaves the ``render`` around the tool
trace and before ``turn_done``.
"""

import asyncio
import unittest

from artmind_canvas_backend.canvas_backend import CanvasBackend
from artmind_canvas_backend.render_events import (
    document_card,
    graph_view_card,
    placement_card,
    provenance_card,
    render_event,
)
from artmind_canvas_backend.show_card_tool import (
    QUALIFIED_TOOL_NAME,
    SHOW_CARD_INPUT_SCHEMA,
    make_show_card_tool,
)


def _invoke(args: dict) -> tuple[dict, "asyncio.Queue[dict]"]:
    """Run the ``show_card`` handler once; return (result, queue)."""
    queue: asyncio.Queue = asyncio.Queue()
    tool = make_show_card_tool(queue)

    async def run() -> dict:
        return await tool.handler(args)

    return asyncio.run(run()), queue


class ShowCardToolTest(unittest.TestCase):
    def test_tool_metadata(self) -> None:
        tool = make_show_card_tool(asyncio.Queue())
        self.assertEqual(tool.name, "show_card")
        self.assertEqual(QUALIFIED_TOOL_NAME, "mcp__canvas__show_card")
        self.assertEqual(
            SHOW_CARD_INPUT_SCHEMA["properties"]["cardType"]["enum"],
            ["provenance", "graph-view", "document", "placement"],
        )

    def test_provenance_enqueues_matching_render(self) -> None:
        result, queue = _invoke(
            {"cardType": "provenance", "domains": ["banking"], "reference": "Acme Corp"}
        )
        self.assertNotIn("is_error", result)
        self.assertEqual(queue.qsize(), 1)
        self.assertEqual(
            queue.get_nowait(),
            render_event(provenance_card(["banking"], reference="Acme Corp")),
        )

    def test_graph_view_enqueues_matching_render(self) -> None:
        _, queue = _invoke(
            {"cardType": "graph-view", "domains": ["banking"], "entityId": "e42"}
        )
        self.assertEqual(
            queue.get_nowait(),
            render_event(graph_view_card(["banking"], entity_id="e42")),
        )

    def test_document_enqueues_matching_render(self) -> None:
        _, queue = _invoke({"cardType": "document", "vaultPath": "notes/x.md"})
        self.assertEqual(
            queue.get_nowait(), render_event(document_card("notes/x.md"))
        )

    def test_placement_enqueues_matching_render(self) -> None:
        _, queue = _invoke(
            {"cardType": "placement", "vaultPath": "inbox/y.md", "domains": ["ops"]}
        )
        self.assertEqual(
            queue.get_nowait(),
            render_event(placement_card("inbox/y.md", domains=["ops"])),
        )

    def test_title_is_forwarded(self) -> None:
        _, queue = _invoke(
            {
                "cardType": "provenance",
                "domains": ["d"],
                "reference": "r",
                "title": "Where from?",
            }
        )
        self.assertEqual(queue.get_nowait()["card"]["props"]["title"], "Where from?")

    def test_provenance_without_selector_is_error(self) -> None:
        result, queue = _invoke({"cardType": "provenance", "domains": ["banking"]})
        self.assertTrue(result.get("is_error"))
        self.assertTrue(queue.empty())

    def test_provenance_without_domains_is_error(self) -> None:
        result, queue = _invoke({"cardType": "provenance", "reference": "Acme"})
        self.assertTrue(result.get("is_error"))
        self.assertTrue(queue.empty())

    def test_document_without_vault_path_is_error(self) -> None:
        result, queue = _invoke({"cardType": "document"})
        self.assertTrue(result.get("is_error"))
        self.assertTrue(queue.empty())

    def test_unknown_card_type_is_error(self) -> None:
        result, queue = _invoke({"cardType": "micro-ui", "domains": ["d"]})
        self.assertTrue(result.get("is_error"))
        self.assertTrue(queue.empty())


class _FakeInner:
    """Minimal AgentBackend that scripts a turn and enqueues a render mid-stream.

    Mimics the SDK: while ``receive_events`` yields the ``tool_call``/
    ``tool_result`` trace, the ``show_card`` handler runs and puts a ``render``
    on the CanvasBackend's queue. We drive that here by invoking the tool at the
    tool_call boundary, exactly where the SDK would.
    """

    def __init__(self, tool, card_args: dict) -> None:
        self._tool = tool
        self._card_args = card_args

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def query(self, prompt: str) -> None:
        return None

    async def receive_events(self):
        yield {"type": "text_delta", "text": "Looking that up…"}
        yield {"type": "block_done", "block": "text"}
        yield {"type": "tool_call", "id": "t1", "name": "show_card", "input": "…"}
        # The SDK invokes the tool here, between tool_use and its result.
        await self._tool.handler(self._card_args)
        yield {"type": "tool_result", "tool_id": "t1", "content": "ok"}
        yield {"type": "turn_done", "turns": 1, "duration_s": 0.1, "cost": None}


class InterleaveTest(unittest.TestCase):
    def test_render_interleaves_around_tool_trace_before_turn_done(self) -> None:
        captured: dict = {}

        def factory(**kwargs):
            captured.update(kwargs)
            tool = make_show_card_tool(backend._render_queue)
            return _FakeInner(
                tool,
                {"cardType": "graph-view", "domains": ["banking"], "reference": "Acme"},
            )

        backend = CanvasBackend(factory)

        async def run():
            await backend.query("how does Acme connect to its subsidiaries?")
            return [ev async for ev in backend.receive_events()]

        events = asyncio.run(run())
        types = [e["type"] for e in events]

        # The inner factory received the canvas MCP server + allowed tool.
        self.assertIn("canvas", captured["mcp_servers"])
        self.assertEqual(captured["allowed_tools"], [QUALIFIED_TOOL_NAME])

        # render lands after the tool trace and strictly before turn_done.
        self.assertIn("render", types)
        self.assertLess(types.index("tool_result"), types.index("render"))
        self.assertLess(types.index("render"), types.index("turn_done"))
        render = events[types.index("render")]
        self.assertEqual(render["card"]["cardType"], "graph-view")
        self.assertEqual(render["card"]["props"]["reference"], "Acme")


if __name__ == "__main__":
    unittest.main()
