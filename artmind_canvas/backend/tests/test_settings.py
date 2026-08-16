"""Global UI settings store + endpoints (ADR 0015)."""

import os
import tempfile
import unittest

from fastapi import FastAPI
from starlette.testclient import TestClient

from artmind_canvas_backend.routes.settings import router as settings_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(settings_router)
    return TestClient(app)


class SettingsApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="canvas-settings-")
        self._prev = os.environ.get("ARTMIND_CANVAS_STATE_DIR")
        os.environ["ARTMIND_CANVAS_STATE_DIR"] = self._tmp
        self.client = _client()

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("ARTMIND_CANVAS_STATE_DIR", None)
        else:
            os.environ["ARTMIND_CANVAS_STATE_DIR"] = self._prev

    def test_defaults_when_absent(self) -> None:
        body = self.client.get("/api/settings").json()
        self.assertFalse(body["chat"]["collapsed"])
        self.assertIsNone(body["chat"]["width"])

    def test_patch_round_trip_and_partial(self) -> None:
        self.client.put("/api/settings", json={"chat": {"width": 420, "collapsed": True}})
        body = self.client.get("/api/settings").json()
        self.assertEqual(body["chat"]["width"], 420)
        self.assertTrue(body["chat"]["collapsed"])
        # editor untouched by the chat-only patch
        self.assertFalse(body["editor"]["collapsed"])

        # patching editor leaves chat intact
        self.client.put("/api/settings", json={"editor": {"width": 600, "collapsed": False}})
        body = self.client.get("/api/settings").json()
        self.assertEqual(body["chat"]["width"], 420)
        self.assertEqual(body["editor"]["width"], 600)


if __name__ == "__main__":
    unittest.main()
