"""What a topic page is built on (spec 2026-11-01 §3.2 step 1).

In graph domains the topic text is resolved with `entity-resolve`. One clear
match anchors the page on that entity: a single exact-name hit, or a top score
of at least ANCHOR_MIN_SCORE that is ANCHOR_MARGIN times the runner-up's. The
anchor carries the entity's synthesis description, its `_observation_set_hash`
(for staleness) and its `entity-context` evidence chunks. Otherwise, and in
chunks-only scopes, the page is anchored on the topic text.
"""
from __future__ import annotations

from artmind.graph_query import read_session

#: entity-resolve scores are RRF (k=60): rank 1 in both the vector and fulltext legs is 2/61 ≈ 0.033.
ANCHOR_MIN_SCORE = 0.03
ANCHOR_MARGIN = 1.5
ENTITY_CHUNKS = 8

_ENTITY = """
MATCH (e:Entity {_id: $id})
RETURN e.key AS key, e.name AS name, e.entity_class AS entity_class, e.description AS description,
       e._observation_set_hash AS observation_set_hash
"""


def _clear_match(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    exact = [r for r in rows if "exact" in (r.get("matched_by") or [])]
    if exact:
        return exact[0] if len(exact) == 1 else None
    top = rows[0].get("score") or 0.0
    second = (rows[1].get("score") or 0.0) if len(rows) > 1 else 0.0
    return rows[0] if top >= ANCHOR_MIN_SCORE and top >= ANCHOR_MARGIN * second else None


def choose_anchor(topic: str, *, graph_domains: list[str], resolve=None, context=None,
                  session_factory=None) -> dict:
    if not graph_domains:
        return {"kind": "topic"}
    if resolve is None:
        from artmind.vector_query import entity_resolve as resolve
    hit = _clear_match(resolve(graph_domains, topic, 5).get("rows") or [])
    if hit is None or not (hit.get("entity") or {}).get("_id"):
        return {"kind": "topic"}
    entity_id = hit["entity"]["_id"]
    with (session_factory or read_session)() as session:
        rec = session.run(_ENTITY, id=entity_id).single()
    if rec is None:
        return {"kind": "topic"}
    if context is None:
        from artmind.graph_query import entity_context as context
    ctx_rows = context(graph_domains, entity_id, ENTITY_CHUNKS, query=topic).get("rows") or []
    chunks = [c for c in ((ctx_rows[0].get("chunks") or []) if ctx_rows else []) if isinstance(c, dict) and c.get("id")]
    return {
        "kind": "entity", "id": entity_id, "key": rec["key"], "name": rec["name"],
        "entity_class": rec["entity_class"], "description": rec["description"],
        "observation_set_hash": rec["observation_set_hash"],
        "chunks": [{"id": c["id"], "doc_id": c.get("doc_id"), "text": c.get("text") or ""} for c in chunks],
    }
