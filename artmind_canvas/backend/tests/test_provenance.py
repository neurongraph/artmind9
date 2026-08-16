"""Provenance endpoint + artmind-query seam (ADR 0014, ADR 0003).

Hermetic: the route reaches artmind only through ``artmind_query`` (a POST to the
serve daemon), so we patch that seam — no daemon, no Neo4j. unittest.TestCase so
it runs under pytest or ``python -m unittest``.
"""

import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from artmind_canvas_backend import artmind_query
from artmind_canvas_backend.artmind_query import ArtmindQueryError, ServeUnavailable
from artmind_canvas_backend.routes.provenance import router as provenance_router

# A representative `query entity-context` result (one row), trimmed to the fields
# the route reads — the shape documented in graph_query.entity_context.
CONTEXT_ROW = {
    "rows": [
        {
            "entityData": {
                "id": "ent-1",
                "name": "Acme Corp",
                "entity_class": "Organization",
                "type": "company",
                "description": "A maker of anvils.",
                "domain": "business",
                "embedding": [0.1, 0.2],  # must be absent from output
                "_prop_sources": {"x": 1},  # must be absent from output
            },
            "connections": [{"type": "SUPPLIES", "properties": {}, "target": {"id": "ent-2"}}],
            "chunks": [
                {
                    "id": "docA_003",
                    "name": "Chunk 3/10",
                    "doc_id": "docA",
                    "domain": "business",
                    "valid_to": None,
                    "text": "Acme Corp manufactures anvils in Nevada.",
                    "document": {
                        "id": "docA",
                        "name": "Acme profile.md",
                        "domain": "business",
                        "superseded_by": None,
                    },
                }
            ],
            "more_chunks": [{"id": "docA_004", "name": "Chunk 4/10", "doc_id": "docA"}],
            "chat_sources": [
                {"id": "chat-9", "raw_text": "tell me about Acme", "created_at": "2026-08-01"}
            ],
        }
    ]
}


def _app() -> TestClient:
    app = FastAPI()
    app.include_router(provenance_router)
    return TestClient(app, raise_server_exceptions=False)


class ProvenanceRouteTest(unittest.TestCase):
    def test_entity_id_path_shapes_payload(self) -> None:
        with patch(
            "artmind_canvas_backend.routes.provenance.entity_context",
            new=AsyncMock(return_value=CONTEXT_ROW),
        ) as ctx:
            r = _app().get("/api/provenance", params={"domain": "business", "entityId": "ent-1"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["entity"]["name"], "Acme Corp")
        self.assertEqual(body["entity"]["entityClass"], "Organization")
        # internal props never leak
        self.assertNotIn("embedding", body["entity"])
        self.assertNotIn("_prop_sources", body["entity"])
        self.assertEqual(len(body["sources"]), 1)
        self.assertEqual(body["sources"][0]["document"]["name"], "Acme profile.md")
        self.assertEqual(body["sources"][0]["chunkId"], "docA_003")
        self.assertEqual(body["moreCount"], 1)
        self.assertEqual(body["chatSources"][0]["id"], "chat-9")
        self.assertIsNone(body["resolvedFrom"])
        # entity id passed straight through; includeChunks default is 8
        ctx.assert_awaited_once()
        self.assertEqual(ctx.await_args.args[0], "ent-1")
        self.assertEqual(ctx.await_args.kwargs["include_chunks"], 8)

    def test_reference_path_resolves_then_contexts(self) -> None:
        resolve = AsyncMock(return_value={"rows": [{"entity": {"id": "ent-1"}}]})
        ctx = AsyncMock(return_value=CONTEXT_ROW)
        with patch("artmind_canvas_backend.routes.provenance.entity_resolve", new=resolve), patch(
            "artmind_canvas_backend.routes.provenance.entity_context", new=ctx
        ):
            r = _app().get("/api/provenance", params={"domain": "business", "reference": "Acme"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["resolvedFrom"], "Acme")
        resolve.assert_awaited_once()
        # the resolved id is what we contextualize
        self.assertEqual(ctx.await_args.args[0], "ent-1")

    def test_missing_selector_is_400(self) -> None:
        r = _app().get("/api/provenance", params={"domain": "business"})
        self.assertEqual(r.status_code, 400)

    def test_unresolvable_reference_is_404(self) -> None:
        with patch(
            "artmind_canvas_backend.routes.provenance.entity_resolve",
            new=AsyncMock(return_value={"rows": []}),
        ):
            r = _app().get("/api/provenance", params={"domain": "business", "reference": "Nobody"})
        self.assertEqual(r.status_code, 404)

    def test_serve_unavailable_is_503(self) -> None:
        with patch(
            "artmind_canvas_backend.routes.provenance.entity_context",
            new=AsyncMock(side_effect=ServeUnavailable("down")),
        ):
            r = _app().get("/api/provenance", params={"domain": "business", "entityId": "ent-1"})
        self.assertEqual(r.status_code, 503)

    def test_query_error_is_502(self) -> None:
        with patch(
            "artmind_canvas_backend.routes.provenance.entity_context",
            new=AsyncMock(side_effect=ArtmindQueryError("boom", exit_code=1)),
        ):
            r = _app().get("/api/provenance", params={"domain": "business", "entityId": "ent-1"})
        self.assertEqual(r.status_code, 502)


class ArtmindQuerySeamTest(unittest.TestCase):
    """The transport itself: it must parse the daemon's {exit_code,output,stderr}."""

    class _Resp:
        def __init__(self, payload: dict) -> None:
            self._data = json.dumps(payload).encode()

        def read(self) -> bytes:
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *a) -> None:
            return None

    def test_parses_compact_output(self) -> None:
        daemon = {"exit_code": 0, "output": json.dumps({"rows": [1, 2]}), "stderr": ""}
        with patch("urllib.request.urlopen", return_value=self._Resp(daemon)):
            out = artmind_query._post_query(["query", "entity-context", "--compact"])
        self.assertEqual(out, {"rows": [1, 2]})

    def test_nonzero_exit_raises_query_error(self) -> None:
        daemon = {"exit_code": 2, "output": "", "stderr": "bad domain"}
        with patch("urllib.request.urlopen", return_value=self._Resp(daemon)):
            with self.assertRaises(ArtmindQueryError) as caught:
                artmind_query._post_query(["query", "x", "--compact"])
        self.assertEqual(caught.exception.exit_code, 2)

    def test_connection_error_raises_serve_unavailable(self) -> None:
        import urllib.error

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with self.assertRaises(ServeUnavailable):
                artmind_query._post_query(["query", "x", "--compact"])

    def test_run_query_appends_compact(self) -> None:
        captured: list[list[str]] = []

        def fake(args: list[str]) -> dict:
            captured.append(args)
            return {}

        import asyncio

        with patch("artmind_canvas_backend.artmind_query._post_query", side_effect=fake):
            asyncio.run(artmind_query.run_query(["entity-context", "--entityId", "z"]))
        self.assertEqual(captured[0], ["query", "entity-context", "--entityId", "z", "--compact"])


if __name__ == "__main__":
    unittest.main()
