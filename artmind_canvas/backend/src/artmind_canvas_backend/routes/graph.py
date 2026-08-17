"""Node-link subgraph reads for the `graph-view` Card (ADRs 0003/0004/0008).

Renders a **filtered one-hop neighborhood** of the knowledge graph: an anchor
entity and the entities it connects to, as node-link JSON the Card draws with
Cytoscape. Like every canvas graph read it stays a **pure client** — the data
comes from ``artmind serve`` (``query entity-context``, see ``artmind_query``),
never a Neo4j driver here.

artmind owns the graph and the Cypher; entity-context returns an anchor plus a
``connections[]`` list (``{type, properties, target:{id, name, label}}``). This
endpoint's only job is to *reshape* that into ``{nodes, edges}`` the Card can
render — assembling node-link JSON is exactly the net-new client work ADR 0003
allows. No graph logic, no Cypher, lives here.

Given an ``entityId`` we call ``entity-context`` directly; given a free-text
``reference`` we first ``entity-resolve`` to a canonical entity, then context.
"""

from fastapi import APIRouter, HTTPException, Query

from artmind_canvas_backend.artmind_query import (
    ArtmindQueryError,
    ServeUnavailable,
    entity_context,
    entity_resolve,
)

router = APIRouter()


def _entity_class(labels: object) -> str | None:
    """``labels(n)`` (a list, e.g. ``["Entity"]``) → a single class label or None."""
    if isinstance(labels, list):
        meaningful = [x for x in labels if x and x != "Entity"]
        return meaningful[0] if meaningful else (labels[0] if labels else None)
    if isinstance(labels, str):
        return labels
    return None


def _shape_graph(row: dict, *, max_neighbors: int) -> dict:
    """An ``entity_context`` row → ``{nodes, edges, anchor, truncated}``.

    Nodes are deduped by id (a neighbor reachable by two relationship types is
    one node with two edges). Edges are keyed uniquely so parallel relationships
    between the same pair survive. Relationships are undirected in the graph
    (``-[r]-``); the Card treats them as such.
    """
    entity_data = row.get("entityData") or {}
    anchor_id = entity_data.get("id")
    anchor = {
        "id": anchor_id,
        "name": entity_data.get("name"),
        "entityClass": entity_data.get("entity_class") or _entity_class(entity_data.get("label")),
        "anchor": True,
    }

    nodes: dict[str, dict] = {anchor_id: anchor} if anchor_id else {}
    edges: list[dict] = []
    truncated = False

    for i, conn in enumerate(row.get("connections") or []):
        if not isinstance(conn, dict):
            continue
        target = conn.get("target") or {}
        tid = target.get("id")
        if not tid or not anchor_id:
            continue  # can't key an edge without both endpoints
        if tid not in nodes and len(nodes) - 1 >= max_neighbors:
            truncated = True
            continue  # neighbour cap hit; keep edges only among nodes we kept
        if tid not in nodes:
            nodes[tid] = {
                "id": tid,
                "name": target.get("name"),
                "entityClass": _entity_class(target.get("label")),
                "anchor": False,
            }
        rel_type = conn.get("type") or "RELATED"
        edges.append(
            {
                "id": f"{anchor_id}->{tid}:{rel_type}#{i}",
                "source": anchor_id,
                "target": tid,
                "type": rel_type,
            }
        )

    return {
        "nodes": list(nodes.values()),
        "edges": edges,
        "anchor": anchor,
        "truncated": truncated,
    }


@router.get("/api/graph")
async def get_graph(
    domain: list[str] = Query(..., description="Domain(s) to scope the read; repeatable"),
    entityId: str | None = Query(None, description="Canonical entity id (skips resolve)"),
    reference: str | None = Query(None, description="Free-text entity name/description to resolve"),
    maxNeighbors: int = Query(60, ge=1, le=250, description="Cap on neighbour nodes rendered"),
):
    """Return a one-hop node-link neighbourhood for an anchor entity.

    ``{nodes, edges, anchor, resolvedFrom, domains, truncated, mode}`` — ``mode``
    is always ``"neighborhood"`` in this cut (path/conflicts modes are future).
    """
    if not entityId and not reference:
        raise HTTPException(status_code=400, detail="provide entityId or reference")

    try:
        resolved_id = entityId
        if resolved_id is None:
            resolve = await entity_resolve(reference or "", domain)
            rows = resolve.get("rows") or []
            if not rows:
                raise HTTPException(status_code=404, detail=f"no entity matched {reference!r}")
            resolved_id = (rows[0].get("entity") or {}).get("id")
            if not resolved_id:
                raise HTTPException(status_code=404, detail=f"no entity id for {reference!r}")

        # Topology only — chunks are irrelevant to a node-link view.
        context = await entity_context(resolved_id, domain, include_chunks=0)
    except ServeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ArtmindQueryError as exc:
        raise HTTPException(status_code=502, detail=f"artmind query failed: {exc}")

    rows = context.get("rows") or []
    if not rows:
        raise HTTPException(status_code=404, detail=f"entity {resolved_id!r} not found")

    payload = _shape_graph(rows[0], max_neighbors=maxNeighbors)
    payload["resolvedFrom"] = reference if entityId is None else None
    payload["domains"] = domain
    payload["mode"] = "neighborhood"
    return payload
