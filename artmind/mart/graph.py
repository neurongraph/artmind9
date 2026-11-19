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
        (r.get("props") or {}).pop("name_embedding", None)
    return rows


def happenings(domains, classes, dim_props=()) -> list[dict]:
    """Every fact entity of `classes`. `dim_props`: the property names the declaration says name dimension members;
    each fact's values for them come back in `dim_props` (links, spec 2026-11-19)."""
    cypher = f"""
    MATCH (e:Entity) WHERE {domain_predicate("e")} AND e.entity_class IN $classes
    OPTIONAL MATCH (e)-[:AGGREGATES]->(o:Observation)
    WITH e, collect(o.id) AS obs, collect(o.chunk_id) AS chunks, collect(DISTINCT o.doc_id) AS docs,
         min(o._valid_from) AS first, max(o._valid_from) AS last
    RETURN e.key AS key, e.name AS name, e.entity_class AS class, e._valid_from AS date, e.type AS type,
           e.outcome AS outcome, coalesce(e.participants, []) AS participants, obs, chunks, docs, first, last,
           [k IN $dimProps WHERE e[k] IS NOT NULL | [k, e[k]]] AS _dim_pairs"""
    rows = graph_query._run_read_query(cypher, {"domains": list(domains), "classes": list(classes),
                                                "dimProps": list(dim_props)})
    for r in rows:  # only the declared keys cross the wire (no APOC, no embeddings)
        pairs = r.pop("_dim_pairs", None) or []
        r.setdefault("dim_props", {k: v for k, v in pairs if v not in ("", [])})
    return rows


def same_chunk_members(domains, fact_classes, dim_class) -> list[dict]:
    """`{fact_key, member_key}`: dimension entities extracted from the same chunks as each fact (source 3 of links)."""
    cypher = f"""
    MATCH (f:Entity)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(c:DocChunk)
    WHERE {domain_predicate("f")} AND f.entity_class IN $facts
    MATCH (c)<-[:EXTRACTED_FROM]-(:Observation)<-[:AGGREGATES]-(d:Entity)
    WHERE d.entity_class = $dim AND {domain_predicate("d")}
    RETURN DISTINCT f.key AS fact_key, d.key AS member_key"""
    return graph_query._run_read_query(cypher, {"domains": list(domains), "facts": list(fact_classes), "dim": dim_class})


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
