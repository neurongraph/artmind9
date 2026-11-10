"""Read-only graph reads for the mart (no writes; `graph_query._run_read_query`)."""
from __future__ import annotations

from artmind import graph_query
from artmind.graph_query import domain_predicate


def members(domains, entity_class) -> list[dict]:
    cypher = f"""
    MATCH (n:Entity) WHERE {domain_predicate("n")} AND n.entity_class = $cls
    RETURN n.key AS key, properties(n) AS props"""
    rows = graph_query._run_read_query(cypher, {"domains": list(domains), "cls": entity_class})
    for r in rows:
        (r.get("props") or {}).pop("embedding", None)
    return rows


def happenings(domains, classes) -> list[dict]:
    cypher = f"""
    MATCH (e:Entity) WHERE {domain_predicate("e")} AND e.entity_class IN $classes
    OPTIONAL MATCH (e)-[:AGGREGATES]->(o:Observation)
    WITH e, collect(o.id) AS obs, collect(o.chunk_id) AS chunks, collect(DISTINCT o.doc_id) AS docs,
         min(o._valid_from) AS first, max(o._valid_from) AS last
    RETURN e.key AS key, e.name AS name, e.entity_class AS class, e._valid_from AS date, e.type AS type,
           e.outcome AS outcome, coalesce(e.participants, []) AS participants, obs, chunks, docs, first, last"""
    return graph_query._run_read_query(cypher, {"domains": list(domains), "classes": list(classes)})


def mentions(domains, classes) -> list[dict]:
    cypher = f"""
    MATCH (e:Entity)-[:AGGREGATES]->(o:Observation)
    WHERE {domain_predicate("e")} AND e.entity_class IN $classes
    RETURN o.id AS id, e.key AS entity_key, e.entity_class AS class, o._valid_from AS date, o.type AS type,
           o.doc_id AS doc_id, o.chunk_id AS chunk_id, head(coalesce(o.context, [])) AS sentence"""
    return graph_query._run_read_query(cypher, {"domains": list(domains), "classes": list(classes)})


def bridges(domains, fact_classes, dim_classes) -> list[dict]:
    cypher = f"""
    MATCH (f:Entity)-[r:RELATES_TO]-(d:Entity)
    WHERE {domain_predicate("f")} AND f.entity_class IN $facts AND d.entity_class IN $dims
    RETURN DISTINCT f.key AS fact_key, d.key AS member_key, d.entity_class AS member_class, r.rel_type AS role"""
    return graph_query._run_read_query(cypher, {"domains": list(domains), "facts": list(fact_classes),
                                                "dims": list(dim_classes)})
