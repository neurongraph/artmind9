"""Render-event contract: Card-spec factories + the canned test hooks (ADR 0014).

unittest.TestCase so it runs under pytest or ``python -m unittest``. The canned
sequences let the render wire be verified offline — no agent process, no auth.
"""

import asyncio
import unittest

from artmind_canvas_backend.canvas_backend import CanvasBackend
from artmind_canvas_backend.render_events import (
    document_card,
    graph_view_card,
    micro_ui_card,
    placement_card,
    provenance_card,
    render_event,
)


class CardFactoryTest(unittest.TestCase):
    def test_render_event_wraps_card(self) -> None:
        ev = render_event({"cardType": "document", "props": {}})
        self.assertEqual(ev["type"], "render")
        self.assertEqual(ev["card"]["cardType"], "document")

    def test_document_card_omits_absent_block_id(self) -> None:
        self.assertEqual(document_card("a.md")["props"], {"vaultPath": "a.md"})
        self.assertEqual(document_card("a.md", "b1")["props"]["blockId"], "b1")

    def test_provenance_card_carries_selectors(self) -> None:
        card = provenance_card(["banking.cases"], reference="incident timeline")
        self.assertEqual(card["cardType"], "provenance")
        self.assertEqual(card["props"]["domains"], ["banking.cases"])
        self.assertEqual(card["props"]["reference"], "incident timeline")
        self.assertNotIn("entityId", card["props"])

    def test_micro_ui_card_carries_html_on_props(self) -> None:
        card = micro_ui_card("<p>hi</p>", title="demo")
        self.assertEqual(card["cardType"], "micro-ui")
        # html rides on props (not a top-level card.html) so it round-trips
        # through the declarative props path and board persistence.
        self.assertEqual(card["props"]["html"], "<p>hi</p>")
        self.assertEqual(card["props"]["title"], "demo")

    def test_micro_ui_card_omits_absent_title(self) -> None:
        self.assertNotIn("title", micro_ui_card("<p>hi</p>")["props"])

    def test_placement_card_carries_vault_path_and_optional_domains(self) -> None:
        card = placement_card("inbox/draft.md", domains=["banking"], title="file this")
        self.assertEqual(card["cardType"], "placement")
        self.assertEqual(card["props"]["vaultPath"], "inbox/draft.md")
        self.assertEqual(card["props"]["domains"], ["banking"])
        self.assertEqual(card["props"]["title"], "file this")

    def test_placement_card_omits_absent_domains_and_title(self) -> None:
        props = placement_card("inbox/draft.md")["props"]
        self.assertEqual(props, {"vaultPath": "inbox/draft.md"})

    def test_graph_view_card_carries_selectors(self) -> None:
        card = graph_view_card(["banking"], reference="Acme Corp", title="neighbourhood")
        self.assertEqual(card["cardType"], "graph-view")
        self.assertEqual(card["props"]["domains"], ["banking"])
        self.assertEqual(card["props"]["reference"], "Acme Corp")
        self.assertEqual(card["props"]["title"], "neighbourhood")
        self.assertNotIn("entityId", card["props"])

    def test_graph_view_card_omits_absent_optionals(self) -> None:
        props = graph_view_card(["banking"], entity_id="e1")["props"]
        self.assertEqual(props, {"domains": ["banking"], "entityId": "e1"})


class CannedSequenceTest(unittest.TestCase):
    def _drain(self, backend: CanvasBackend, prompt: str) -> list[dict]:
        async def run():
            await backend.query(prompt)
            return [ev async for ev in backend.receive_events()]

        return asyncio.run(run())

    def test_microui_test_hook_emits_render_and_turn_done(self) -> None:
        backend = CanvasBackend(lambda: (_ for _ in ()).throw(AssertionError("no inner")))
        events = self._drain(backend, "/microui-test my widget")
        types = [e["type"] for e in events]
        self.assertEqual(types, ["text_delta", "block_done", "render", "turn_done"])
        card = events[2]["card"]
        self.assertEqual(card["cardType"], "micro-ui")
        self.assertIn("<script>", card["props"]["html"])
        self.assertEqual(card["props"]["title"], "my widget")

    def test_microui_test_hook_default_title(self) -> None:
        backend = CanvasBackend(lambda: (_ for _ in ()).throw(AssertionError("no inner")))
        events = self._drain(backend, "/microui-test")
        self.assertEqual(events[2]["card"]["props"]["title"], "demo widget")

    def test_canned_hook_never_builds_inner_backend(self) -> None:
        # The lazy inner factory must not fire for a canned hook.
        def boom() -> None:
            raise AssertionError("inner backend should not be built for canned hooks")

        backend = CanvasBackend(boom)
        self._drain(backend, "/microui-test")  # would raise if the factory ran


if __name__ == "__main__":
    unittest.main()
