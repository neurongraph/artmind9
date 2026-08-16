"""Vault read/write + conditional-write + listing (ADR 0015).

unittest.TestCase so it runs under pytest or ``python -m unittest``. Mounts only the
vault router and points ARTMIND_VAULT_DIR at a temp dir — no artmind import needed.
"""

import os
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from starlette.testclient import TestClient

from artmind_canvas_backend.routes.vault import router as vault_router


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(vault_router)
    return TestClient(app)


class VaultWriteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="canvas-vault-")
        self._prev = os.environ.get("ARTMIND_VAULT_DIR")
        os.environ["ARTMIND_VAULT_DIR"] = self._tmp
        (Path(self._tmp) / "note.md").write_text("# hello\n", encoding="utf-8")
        self.client = _client()

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("ARTMIND_VAULT_DIR", None)
        else:
            os.environ["ARTMIND_VAULT_DIR"] = self._prev

    def test_read_returns_content_and_version(self) -> None:
        r = self.client.get("/api/vault/file", params={"path": "note.md"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["content"], "# hello\n")
        self.assertTrue(body["version"])

    def test_conditional_write_round_trip(self) -> None:
        version = self.client.get("/api/vault/file", params={"path": "note.md"}).json()["version"]
        r = self.client.put(
            "/api/vault/file",
            json={"path": "note.md", "content": "# edited\n", "baseVersion": version},
        )
        self.assertEqual(r.status_code, 200)
        new_version = r.json()["version"]
        self.assertNotEqual(new_version, version)
        # content persisted
        self.assertEqual(
            self.client.get("/api/vault/file", params={"path": "note.md"}).json()["content"],
            "# edited\n",
        )

    def test_stale_version_is_409_with_current(self) -> None:
        r = self.client.put(
            "/api/vault/file",
            json={"path": "note.md", "content": "x\n", "baseVersion": "deadbeef"},
        )
        self.assertEqual(r.status_code, 409)
        self.assertIn("currentVersion", r.json()["detail"])
        # the real file was NOT clobbered
        self.assertEqual(
            self.client.get("/api/vault/file", params={"path": "note.md"}).json()["content"],
            "# hello\n",
        )

    def test_create_new_file_with_null_base(self) -> None:
        r = self.client.put(
            "/api/vault/file",
            json={"path": "sub/new.md", "content": "# new\n", "baseVersion": None},
        )
        self.assertEqual(r.status_code, 200)
        self.assertTrue((Path(self._tmp) / "sub" / "new.md").is_file())

    def test_create_over_existing_is_conflict(self) -> None:
        # baseVersion None means "create", but the file already exists → 409
        r = self.client.put(
            "/api/vault/file",
            json={"path": "note.md", "content": "x\n", "baseVersion": None},
        )
        self.assertEqual(r.status_code, 409)

    def test_non_markdown_write_rejected(self) -> None:
        r = self.client.put(
            "/api/vault/file",
            json={"path": "evil.txt", "content": "x", "baseVersion": None},
        )
        self.assertEqual(r.status_code, 400)

    def test_traversal_rejected(self) -> None:
        r = self.client.put(
            "/api/vault/file",
            json={"path": "../escape.md", "content": "x", "baseVersion": None},
        )
        self.assertEqual(r.status_code, 400)

    def test_list_surfaces_markdown_only(self) -> None:
        (Path(self._tmp) / "img.png").write_bytes(b"\x89PNG")
        (Path(self._tmp) / "area").mkdir()
        body = self.client.get("/api/vault/list").json()
        self.assertIn("note.md", body["files"])
        self.assertNotIn("img.png", body["files"])
        self.assertIn("area", body["dirs"])


if __name__ == "__main__":
    unittest.main()
