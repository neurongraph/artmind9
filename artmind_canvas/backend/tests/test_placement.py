"""Placement Card: frontmatter shaping + propose/confirm routes (ADR 0012, A5/A6).

Hermetic. *Propose* reaches artmind only through the ``run_query`` seam, which we
patch (no serve daemon, no LLM). *Confirm* writes through the ordinary Vault write
against a temp ``ARTMIND_VAULT_DIR``. unittest.TestCase so it runs under pytest or
``python -m unittest``.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from artmind_canvas_backend.artmind_query import ArtmindQueryError, ServeUnavailable
from artmind_canvas_backend.frontmatter import merge_frontmatter, split_frontmatter
from artmind_canvas_backend.routes.placement import router as placement_router

# A representative A6 proposal (query propose-placement --compact), trimmed to the
# fields the Card reads — shape from artmind.placement._normalize_proposal.
PROPOSAL = {
    "text_length": 812,
    "vocabulary": {},
    "proposals": {
        "domain": {"value": "banking", "confidence": 0.9, "known": True},
        "area": {"value": "compliance", "confidence": 0.7, "known": True},
        "project": {"value": "kyc-refresh", "confidence": 0.4, "known": False},
        "tags": {"value": ["aml", "onboarding"], "confidence": 0.6, "known": [True, False]},
        "target_file_hint": "compliance/kyc-refresh.md",
        "reason": "Mentions KYC refresh under the AML programme.",
    },
    "model": "claude-opus-4-8",
}


# ---- frontmatter unit tests -------------------------------------------------


class FrontmatterTest(unittest.TestCase):
    def test_split_no_frontmatter(self) -> None:
        meta, body = split_frontmatter("# just a heading\n\ntext")
        self.assertEqual(meta, {})
        self.assertEqual(body, "# just a heading\n\ntext")

    def test_split_round_trips_meta_and_body(self) -> None:
        meta, body = split_frontmatter("---\ntitle: Hi\narea: x\n---\n\n# Body\n")
        self.assertEqual(meta, {"title": "Hi", "area": "x"})
        self.assertEqual(body, "# Body\n")

    def test_merge_adds_frontmatter_to_bare_doc(self) -> None:
        out = merge_frontmatter("# Body\n", area="compliance", project="kyc", tags=["aml"])
        meta, body = split_frontmatter(out)
        self.assertEqual(meta["area"], "compliance")
        self.assertEqual(meta["project"], "kyc")
        self.assertEqual(meta["tags"], ["aml"])
        self.assertEqual(body, "# Body\n")

    def test_merge_preserves_existing_keys_and_body(self) -> None:
        src = "---\ntitle: Kept\ncustom: value\n---\n\n# Body\n\npara\n"
        out = merge_frontmatter(src, area="ops", tags=["t1", "t2"])
        meta, body = split_frontmatter(out)
        self.assertEqual(meta["title"], "Kept")  # untouched existing key
        self.assertEqual(meta["custom"], "value")  # non-filing key survives
        self.assertEqual(meta["area"], "ops")
        self.assertEqual(meta["tags"], ["t1", "t2"])
        self.assertEqual(body, "# Body\n\npara\n")

    def test_merge_none_leaves_facet_untouched(self) -> None:
        src = "---\narea: original\n---\n\nbody\n"
        out = merge_frontmatter(src, area=None, project="p")
        meta, _ = split_frontmatter(out)
        self.assertEqual(meta["area"], "original")
        self.assertEqual(meta["project"], "p")

    def test_merge_empty_tags_clears(self) -> None:
        src = "---\ntags:\n- old\n---\n\nbody\n"
        out = merge_frontmatter(src, tags=[])
        meta, _ = split_frontmatter(out)
        self.assertEqual(meta["tags"], [])

    def test_merge_never_writes_domain(self) -> None:
        # domain is sourced from --domain at ingest, never frontmatter.
        out = merge_frontmatter("body\n", area="a", project="p", tags=["t"])
        meta, _ = split_frontmatter(out)
        self.assertNotIn("domain", meta)


# ---- route tests ------------------------------------------------------------


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(placement_router)
    return TestClient(app, raise_server_exceptions=False)


class PlacementRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="canvas-placement-")
        self._prev = os.environ.get("ARTMIND_VAULT_DIR")
        os.environ["ARTMIND_VAULT_DIR"] = self._tmp
        self.client = _client()

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("ARTMIND_VAULT_DIR", None)
        else:
            os.environ["ARTMIND_VAULT_DIR"] = self._prev

    def _write(self, rel: str, content: str) -> None:
        p = Path(self._tmp) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    # -- propose --------------------------------------------------------------

    def test_propose_strips_frontmatter_and_threads_domains(self) -> None:
        self._write("doc.md", "---\ntitle: Old\n---\n\n# Body\n\ncontent here\n")
        run = AsyncMock(return_value=PROPOSAL)
        with patch("artmind_canvas_backend.routes.placement.run_query", new=run):
            r = self.client.post(
                "/api/placement/propose",
                json={"vaultPath": "doc.md", "domains": ["banking", "legal"]},
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["proposal"], PROPOSAL)
        self.assertEqual(body["vaultPath"], "doc.md")
        self.assertTrue(body["version"])  # on-disk version carried for confirm

        args = run.await_args.args[0]
        self.assertEqual(args[0], "propose-placement")
        # the frontmatter was stripped: the body (not the YAML header) is the text
        text = args[args.index("--text") + 1]
        self.assertNotIn("title: Old", text)
        self.assertIn("# Body", text)
        # both domains threaded as repeatable --domain flags
        self.assertEqual(args.count("--domain"), 2)
        self.assertIn("banking", args)
        self.assertIn("legal", args)

    def test_propose_accepts_raw_text_draft(self) -> None:
        run = AsyncMock(return_value=PROPOSAL)
        with patch("artmind_canvas_backend.routes.placement.run_query", new=run):
            r = self.client.post(
                "/api/placement/propose",
                json={"text": "an unsaved draft", "context": "board meeting"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["version"])  # no file, no version
        args = run.await_args.args[0]
        self.assertEqual(args[args.index("--text") + 1], "an unsaved draft")
        self.assertEqual(args[args.index("--context") + 1], "board meeting")

    def test_propose_missing_file_is_404(self) -> None:
        with patch(
            "artmind_canvas_backend.routes.placement.run_query", new=AsyncMock()
        ):
            r = self.client.post("/api/placement/propose", json={"vaultPath": "nope.md"})
        self.assertEqual(r.status_code, 404)

    def test_propose_without_source_is_400(self) -> None:
        r = self.client.post("/api/placement/propose", json={})
        self.assertEqual(r.status_code, 400)

    def test_propose_serve_unavailable_is_503(self) -> None:
        self._write("doc.md", "body\n")
        with patch(
            "artmind_canvas_backend.routes.placement.run_query",
            new=AsyncMock(side_effect=ServeUnavailable("down")),
        ):
            r = self.client.post("/api/placement/propose", json={"vaultPath": "doc.md"})
        self.assertEqual(r.status_code, 503)

    def test_propose_query_error_is_502(self) -> None:
        self._write("doc.md", "body\n")
        with patch(
            "artmind_canvas_backend.routes.placement.run_query",
            new=AsyncMock(side_effect=ArtmindQueryError("boom", exit_code=1)),
        ):
            r = self.client.post("/api/placement/propose", json={"vaultPath": "doc.md"})
        self.assertEqual(r.status_code, 502)

    # -- confirm --------------------------------------------------------------

    def test_confirm_writes_frontmatter_and_publishes(self) -> None:
        self._write("doc.md", "---\ntitle: Keep\n---\n\n# Body\n\ntext\n")
        from artmind_canvas_backend.vault import read_vault_file

        _, version = read_vault_file("doc.md")
        with patch("artmind_canvas_backend.routes.placement.publish_change") as pub:
            r = self.client.post(
                "/api/placement/confirm",
                json={
                    "path": "doc.md",
                    "baseVersion": version,
                    "placement": {
                        "area": "compliance",
                        "project": "kyc",
                        "tags": ["aml", "onboarding"],
                    },
                },
            )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["version"])
        self.assertEqual(body["reingest"]["triggered"], False)  # deferred to watcher
        pub.assert_called_once_with("document", path="doc.md")

        # facets landed in frontmatter; body + existing key preserved
        content, _ = read_vault_file("doc.md")
        meta, doc_body = split_frontmatter(content)
        self.assertEqual(meta["title"], "Keep")
        self.assertEqual(meta["area"], "compliance")
        self.assertEqual(meta["project"], "kyc")
        self.assertEqual(meta["tags"], ["aml", "onboarding"])
        self.assertEqual(doc_body, "# Body\n\ntext\n")

    def test_confirm_stale_version_is_409_and_does_not_clobber(self) -> None:
        self._write("doc.md", "# original\n")
        with patch("artmind_canvas_backend.routes.placement.publish_change") as pub:
            r = self.client.post(
                "/api/placement/confirm",
                json={
                    "path": "doc.md",
                    "baseVersion": "deadbeef",
                    "placement": {"area": "x"},
                },
            )
        self.assertEqual(r.status_code, 409)
        pub.assert_not_called()
        # file untouched
        from artmind_canvas_backend.vault import read_vault_file

        self.assertEqual(read_vault_file("doc.md")[0], "# original\n")

    def test_confirm_missing_file_is_404(self) -> None:
        r = self.client.post(
            "/api/placement/confirm",
            json={"path": "gone.md", "baseVersion": None, "placement": {"area": "x"}},
        )
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
