"""graph-view Card: node-link assembly from entity-context (ADRs 0004/0008).

Hermetic. The route reaches artmind only through the ``entity_context`` /
``entity_resolve`` seam helpers, which we patch — no serve daemon, no Neo4j, no
LLM. Asserts the reshaping into ``{nodes, edges}``, dedupe, resolve-first, the
neighbour cap, and the 400/404/503/502 mapping. unittest.TestCase so it runs
under pytest or ``python -m unittest``.
"""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from artmind_canvas_backend.artmind_query import ArtmindQueryError, ServeUnavailable
from artmind_canvas_backend.routes.graph import router as graph_router


def _context(entity_data: dict, connections: list[dict]) -> dict:
    """Shape a canned ``query entity-context --compact`` payload (one row)."""
    return {
        "rows": [
            {
                "entityData": entity_data,
                "connections": connections,
                "chunks": [],
                "more_chunks": [],
                "chat_sources": [],
            }
        ]
    }


ANCHOR = {"id": "e1", "name": "Acme Corp", "entity_class": "Organization", "label": ["Entity"]}


def _conn(tid: str, name: str, rel: str) -> dict:
    return {"type": rel, "properties": {}, "target": {"id": tid, "name": name, "label": ["Entity"]}}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(graph_router)
    return TestClient(app, raise_server_exceptions=False)


class GraphRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _client()

    def test_assembles_node_link_from_entity_context(self) -> None:
        ctx = _context(ANCHOR, [_conn("e2", "Jane Doe", "EMPLOYS"), _conn("e3", "London", "LOCATED_IN")])
        with patch(
            "artmind_canvas_backend.routes.graph.entity_context",
            new=AsyncMock(return_value=ctx),
        ) as ec:
            r = self.client.get("/api/graph", params={"domain": "banking", "entityId": "e1"})
        self.assertEqual(r.status_code, 200)
        body = r.json()

        # anchor + two neighbours, deduped by id
        ids = {n["id"] for n in body["nodes"]}
        self.assertEqual(ids, {"e1", "e2", "e3"})
        anchor = next(n for n in body["nodes"] if n["anchor"])
        self.assertEqual(anchor["id"], "e1")
        self.assertEqual(anchor["entityClass"], "Organization")

        # edges anchored on e1, typed, one per connection
        self.assertEqual(len(body["edges"]), 2)
        for edge in body["edges"]:
            self.assertEqual(edge["source"], "e1")
        types = {e["type"]: e["target"] for e in body["edges"]}
        self.assertEqual(types, {"EMPLOYS": "e2", "LOCATED_IN": "e3"})

        self.assertEqual(body["anchor"]["id"], "e1")
        self.assertEqual(body["domains"], ["banking"])
        self.assertEqual(body["mode"], "neighborhood")
        self.assertFalse(body["truncated"])
        self.assertIsNone(body["resolvedFrom"])
        # entityId given → resolve skipped, context called with the id
        self.assertEqual(ec.await_args.args[0], "e1")

    def test_dedupes_neighbor_reached_by_two_rel_types(self) -> None:
        ctx = _context(ANCHOR, [_conn("e2", "Jane", "EMPLOYS"), _conn("e2", "Jane", "MANAGES")])
        with patch(
            "artmind_canvas_backend.routes.graph.entity_context",
            new=AsyncMock(return_value=ctx),
        ):
            r = self.client.get("/api/graph", params={"domain": "banking", "entityId": "e1"})
        body = r.json()
        # one node for e2, but two distinct edges
        self.assertEqual(len([n for n in body["nodes"] if n["id"] == "e2"]), 1)
        self.assertEqual(len(body["edges"]), 2)
        self.assertEqual({e["id"] for e in body["edges"]}.__len__(), 2)  # unique edge ids

    def test_resolves_reference_first(self) -> None:
        resolve = {"rows": [{"entity": {"id": "e1", "name": "Acme Corp"}}]}
        ctx = _context(ANCHOR, [_conn("e2", "Jane", "EMPLOYS")])
        with patch(
            "artmind_canvas_backend.routes.graph.entity_resolve",
            new=AsyncMock(return_value=resolve),
        ) as er, patch(
            "artmind_canvas_backend.routes.graph.entity_context",
            new=AsyncMock(return_value=ctx),
        ) as ec:
            r = self.client.get("/api/graph", params={"domain": "banking", "reference": "acme"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["resolvedFrom"], "acme")
        self.assertEqual(er.await_args.args[0], "acme")  # resolved the free text
        self.assertEqual(ec.await_args.args[0], "e1")  # then context on the resolved id

    def test_max_neighbors_truncates(self) -> None:
        conns = [_conn(f"n{i}", f"Node {i}", "REL") for i in range(10)]
        ctx = _context(ANCHOR, conns)
        with patch(
            "artmind_canvas_backend.routes.graph.entity_context",
            new=AsyncMock(return_value=ctx),
        ):
            r = self.client.get(
                "/api/graph", params={"domain": "banking", "entityId": "e1", "maxNeighbors": 3}
            )
        body = r.json()
        self.assertTrue(body["truncated"])
        neighbours = [n for n in body["nodes"] if not n["anchor"]]
        self.assertEqual(len(neighbours), 3)  # capped
        # every kept edge points at a kept node
        kept = {n["id"] for n in body["nodes"]}
        self.assertTrue(all(e["target"] in kept for e in body["edges"]))

    def test_missing_params_is_400(self) -> None:
        r = self.client.get("/api/graph", params={"domain": "banking"})
        self.assertEqual(r.status_code, 400)

    def test_no_resolve_match_is_404(self) -> None:
        with patch(
            "artmind_canvas_backend.routes.graph.entity_resolve",
            new=AsyncMock(return_value={"rows": []}),
        ):
            r = self.client.get("/api/graph", params={"domain": "banking", "reference": "ghost"})
        self.assertEqual(r.status_code, 404)

    def test_empty_context_is_404(self) -> None:
        with patch(
            "artmind_canvas_backend.routes.graph.entity_context",
            new=AsyncMock(return_value={"rows": []}),
        ):
            r = self.client.get("/api/graph", params={"domain": "banking", "entityId": "e1"})
        self.assertEqual(r.status_code, 404)

    def test_serve_unavailable_is_503(self) -> None:
        with patch(
            "artmind_canvas_backend.routes.graph.entity_context",
            new=AsyncMock(side_effect=ServeUnavailable("down")),
        ):
            r = self.client.get("/api/graph", params={"domain": "banking", "entityId": "e1"})
        self.assertEqual(r.status_code, 503)

    def test_query_error_is_502(self) -> None:
        with patch(
            "artmind_canvas_backend.routes.graph.entity_context",
            new=AsyncMock(side_effect=ArtmindQueryError("boom", exit_code=1)),
        ):
            r = self.client.get("/api/graph", params={"domain": "banking", "entityId": "e1"})
        self.assertEqual(r.status_code, 502)


if __name__ == "__main__":
    unittest.main()
