"""Board-persistence store + endpoints (ADR 0009).

Written as ``unittest.TestCase`` so it runs both under pytest (the user's normal
flow) and via ``python -m unittest`` in environments without pytest installed.
The app under test mounts only the boards router — no chat/agent stack — so these
stay fast and independent of artmind being importable.
"""

import os
import tempfile
import unittest

from fastapi import FastAPI
from starlette.testclient import TestClient

from artmind_canvas_backend.routes.boards import router as boards_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(boards_router)
    return TestClient(app)


class BoardApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="canvas-boards-")
        self._prev = os.environ.get("ARTMIND_CANVAS_STATE_DIR")
        os.environ["ARTMIND_CANVAS_STATE_DIR"] = self._tmp
        self.client = _client()

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("ARTMIND_CANVAS_STATE_DIR", None)
        else:
            os.environ["ARTMIND_CANVAS_STATE_DIR"] = self._prev

    def test_empty_list(self) -> None:
        r = self.client.get("/api/boards")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"boards": []})

    def test_create_get_list(self) -> None:
        r = self.client.post("/api/boards", json={"name": "Research"})
        self.assertEqual(r.status_code, 201)
        board = r.json()
        self.assertEqual(board["name"], "Research")
        self.assertTrue(board["id"])
        self.assertEqual(board["cards"], [])
        bid = board["id"]

        got = self.client.get(f"/api/boards/{bid}")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.json()["id"], bid)

        summaries = self.client.get("/api/boards").json()["boards"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0]["id"], bid)
        self.assertEqual(summaries[0]["name"], "Research")

    def test_default_name(self) -> None:
        # POST with no body → default name, still creates a board.
        r = self.client.post("/api/boards", json={})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["name"], "Untitled board")

    def test_save_cards_and_viewport_round_trip(self) -> None:
        bid = self.client.post("/api/boards", json={"name": "B"}).json()["id"]
        patch = {
            "cards": [
                {
                    "id": "card-0",
                    "cardType": "document",
                    "props": {"vaultPath": "README.md"},
                    "position": {"x": 10, "y": 20},
                    "size": {"width": 400, "height": 300},
                    "filterSpec": None,
                }
            ],
            "viewport": {"x": 5, "y": 6, "zoom": 1.5},
        }
        r = self.client.put(f"/api/boards/{bid}", json=patch)
        self.assertEqual(r.status_code, 200)
        board = r.json()
        self.assertEqual(len(board["cards"]), 1)
        self.assertEqual(board["cards"][0]["position"], {"x": 10, "y": 20})
        self.assertEqual(board["viewport"]["zoom"], 1.5)

        # persisted to disk, reloads identically
        reloaded = self.client.get(f"/api/boards/{bid}").json()
        self.assertEqual(reloaded["cards"][0]["props"], {"vaultPath": "README.md"})
        self.assertEqual(reloaded["cards"][0]["size"], {"width": 400, "height": 300})

    def test_partial_patch_leaves_other_fields(self) -> None:
        bid = self.client.post("/api/boards", json={"name": "B"}).json()["id"]
        # set cards
        self.client.put(
            f"/api/boards/{bid}",
            json={"cards": [{"id": "c1", "cardType": "document", "position": {"x": 1, "y": 2}}]},
        )
        # rename only — cards must survive
        renamed = self.client.put(f"/api/boards/{bid}", json={"name": "B2"}).json()
        self.assertEqual(renamed["name"], "B2")
        self.assertEqual(len(renamed["cards"]), 1)
        self.assertEqual(renamed["cards"][0]["id"], "c1")

    def test_open_documents_round_trip(self) -> None:
        bid = self.client.post("/api/boards", json={"name": "B"}).json()["id"]
        patch = {"openDocuments": ["a.md", "b.md"], "activeDocument": "b.md"}
        board = self.client.put(f"/api/boards/{bid}", json=patch).json()
        self.assertEqual(board["openDocuments"], ["a.md", "b.md"])
        self.assertEqual(board["activeDocument"], "b.md")
        reloaded = self.client.get(f"/api/boards/{bid}").json()
        self.assertEqual(reloaded["activeDocument"], "b.md")

    def test_active_document_self_heals_on_close(self) -> None:
        bid = self.client.post("/api/boards", json={"name": "B"}).json()["id"]
        self.client.put(
            f"/api/boards/{bid}",
            json={"openDocuments": ["a.md", "b.md"], "activeDocument": "b.md"},
        )
        # close the active tab: openDocuments shrinks; active must fall back into range
        healed = self.client.put(f"/api/boards/{bid}", json={"openDocuments": ["a.md"]}).json()
        self.assertEqual(healed["activeDocument"], "a.md")
        # close the last tab → active clears
        empty = self.client.put(f"/api/boards/{bid}", json={"openDocuments": []}).json()
        self.assertIsNone(empty["activeDocument"])

    def test_save_bumps_updated_at(self) -> None:
        board = self.client.post("/api/boards", json={"name": "B"}).json()
        bid, created = board["id"], board["updatedAt"]
        updated = self.client.put(f"/api/boards/{bid}", json={"name": "B2"}).json()
        self.assertGreaterEqual(updated["updatedAt"], created)

    def test_delete_idempotent(self) -> None:
        bid = self.client.post("/api/boards", json={"name": "B"}).json()["id"]
        self.assertEqual(self.client.delete(f"/api/boards/{bid}").status_code, 204)
        self.assertEqual(self.client.get(f"/api/boards/{bid}").status_code, 404)
        # deleting again is a no-op, not an error
        self.assertEqual(self.client.delete(f"/api/boards/{bid}").status_code, 204)

    def test_get_missing_is_404(self) -> None:
        self.assertEqual(self.client.get("/api/boards/deadbeef").status_code, 404)

    def test_put_missing_is_404(self) -> None:
        self.assertEqual(
            self.client.put("/api/boards/deadbeef", json={"name": "x"}).status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
