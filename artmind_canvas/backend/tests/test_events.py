"""Change-event broker + broadcast-on-write wiring (ADR 0008).

unittest.TestCase so it runs under pytest or ``python -m unittest``. The broker is a
tiny asyncio pub/sub; the vault write route publishes to it on a successful save.
"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from starlette.testclient import TestClient

from artmind_canvas_backend import events as events_mod
from artmind_canvas_backend.events import EventBroker
from artmind_canvas_backend.routes.vault import router as vault_router


class EventBrokerTest(unittest.TestCase):
    def test_publish_fans_out_to_all_subscribers(self) -> None:
        async def run():
            broker = EventBroker()
            a = broker.subscribe()
            b = broker.subscribe()
            self.assertEqual(broker.subscriber_count, 2)
            broker.publish({"type": "change", "resource": "document", "path": "x.md"})
            ea = await asyncio.wait_for(a.get(), timeout=1)
            eb = await asyncio.wait_for(b.get(), timeout=1)
            self.assertEqual(ea["path"], "x.md")
            self.assertEqual(eb, ea)
            # after unsubscribe, no further delivery
            broker.unsubscribe(a)
            self.assertEqual(broker.subscriber_count, 1)

        asyncio.run(run())


class WritePublishesChangeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="canvas-events-")
        self._prev = os.environ.get("ARTMIND_VAULT_DIR")
        os.environ["ARTMIND_VAULT_DIR"] = self._tmp
        (Path(self._tmp) / "note.md").write_text("# hi\n", encoding="utf-8")
        # Capture what the route publishes, without standing up an SSE stream.
        self._published: list[dict] = []
        self._orig_publish = events_mod.broker.publish
        events_mod.broker.publish = self._published.append  # type: ignore[assignment]
        app = FastAPI()
        app.include_router(vault_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        events_mod.broker.publish = self._orig_publish  # type: ignore[assignment]
        if self._prev is None:
            os.environ.pop("ARTMIND_VAULT_DIR", None)
        else:
            os.environ["ARTMIND_VAULT_DIR"] = self._prev

    def test_successful_write_publishes_document_change(self) -> None:
        version = self.client.get("/api/vault/file", params={"path": "note.md"}).json()["version"]
        r = self.client.put(
            "/api/vault/file",
            json={"path": "note.md", "content": "# edited\n", "baseVersion": version},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(
            self._published,
            [{"type": "change", "resource": "document", "path": "note.md"}],
        )

    def test_rejected_write_publishes_nothing(self) -> None:
        # A stale-version write is refused (409) and must NOT broadcast a change.
        r = self.client.put(
            "/api/vault/file",
            json={"path": "note.md", "content": "x\n", "baseVersion": "stale"},
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self._published, [])


if __name__ == "__main__":
    unittest.main()
