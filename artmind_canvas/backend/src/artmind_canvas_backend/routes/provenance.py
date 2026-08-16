"""Provenance reads for the `provenance` Card (ADR 0014, A1 provenance data).

Where did a knowledge-graph fact come from? This endpoint answers that for an
entity: the source documents and chunks it was extracted from, plus any chat
mentions. It is the canvas backend's first graph read — and it stays a **pure
client**: every read goes through ``artmind serve`` (see ``artmind_query``),
never a Neo4j driver here.

Given an ``entityId`` we call ``query entity-context`` directly; given a free-text
``reference`` we first ``query entity-resolve`` to a canonical entity, then
context. artmind owns the graph and the Cypher; we only shape its JSON for the
Card.
"""

from fastapi import APIRouter, HTTPException, Query

from artmind_canvas_backend.artmind_query import (
    ArtmindQueryError,
    ServeUnavailable,
    entity_context,
    entity_resolve,
)

router = APIRouter()


def _shape_source(chunk: dict) -> dict:
    """One extracted-from chunk → a provenance source row for the Card."""
    document = chunk.get("document") or {}
    return {
        "chunkId": chunk.get("id"),
        "chunkName": chunk.get("name"),
        "docId": chunk.get("doc_id"),
        "domain": chunk.get("domain"),
        "validTo": chunk.get("valid_to"),
        "snippet": chunk.get("text"),
        "document": {
            "id": document.get("id"),
            "name": document.get("name"),
            "domain": document.get("domain"),
            "supersededBy": document.get("superseded_by"),
        },
    }


def _shape(row: dict) -> dict:
    """An ``entity_context`` row → the provenance payload the Card consumes."""
    entity_data = row.get("entityData") or {}
    chunks = row.get("chunks") or []
    more = row.get("more_chunks") or []
    chats = row.get("chat_sources") or []
    return {
        "entity": {
            "id": entity_data.get("id"),
            "name": entity_data.get("name"),
            "entityClass": entity_data.get("entity_class"),
            "type": entity_data.get("type"),
            "description": entity_data.get("description"),
            "domain": entity_data.get("domain"),
        },
        "sources": [_shape_source(c) for c in chunks],
        "moreCount": len(more),
        "chatSources": [
            {"id": c.get("id"), "snippet": c.get("raw_text"), "createdAt": c.get("created_at")}
            for c in chats
        ],
    }


@router.get("/api/provenance")
async def get_provenance(
    domain: list[str] = Query(..., description="Domain(s) to scope the read; repeatable"),
    entityId: str | None = Query(None, description="Canonical entity id (skips resolve)"),
    reference: str | None = Query(None, description="Free-text entity name/description to resolve"),
    includeChunks: int = Query(8, ge=1, le=50, description="Max source chunks to return"),
):
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

        context = await entity_context(resolved_id, domain, include_chunks=includeChunks)
    except ServeUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ArtmindQueryError as exc:
        raise HTTPException(status_code=502, detail=f"artmind query failed: {exc}")

    rows = context.get("rows") or []
    if not rows:
        raise HTTPException(status_code=404, detail=f"entity {resolved_id!r} not found")
    payload = _shape(rows[0])
    payload["resolvedFrom"] = reference if entityId is None else None
    return payload
