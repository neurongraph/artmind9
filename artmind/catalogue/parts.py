"""One builder per catalogue part (spec 2026-11-02 §4).

A builder takes a domain and returns the `DomainCatalogue` fields its part fills
(`model.PART_FIELDS`); it reads the graph, schema files or the registry and
nothing else, and sorts what it returns, so a cached part equals a live one.
`part_key` says what a part was built from: the cache rebuilds a part when its
key moves, and only that part.
"""
from __future__ import annotations

from typing import Callable

from loguru import logger

from artmind import graph_query
from artmind.catalogue import stamp as stamps
from artmind.catalogue.model import DocRecord, NameEntry
from artmind.graph_query import domain_predicate

NAME_INDEX_MAX = 20_000
RELATIONS_MAX = 50

_CLASSES_CYPHER = f"""
MATCH (e:Entity)
WHERE {domain_predicate("e")} AND e.entity_class IS NOT NULL
RETURN e.entity_class AS name, count(e) AS n
ORDER BY n DESC, name
"""

_RELATIONS_CYPHER = f"""
MATCH (a:Entity)-[r:RELATES_TO]->(:Entity)
WHERE {domain_predicate("a")} AND r.rel_type IS NOT NULL
RETURN r.rel_type AS rel_type, count(r) AS n
ORDER BY n DESC, rel_type
LIMIT $limit
"""

_DOCUMENTS_CYPHER = f"""
MATCH (d:Document|DocumentHistory)
WHERE {domain_predicate("d")}
RETURN d.id AS id, d.name AS name, d._domain AS domain, d.valid_from AS valid_from,
       d:Document AS live,
       EXISTS {{ MATCH (:DocSummary {{level: 'document'}})-[:SUMMARIZES]->(d) }} AS has_summary
"""

_NAMES_CYPHER = f"""
MATCH (e:Entity)
WHERE {domain_predicate("e")} AND e.name IS NOT NULL
RETURN e._id AS _id, e.name AS name, e.aliases AS aliases,
       e.entity_class AS entity_class, coalesce(e._observation_count, 0) AS observation_count,
       e._domain AS domain, COUNT {{ (e)-[:RELATES_TO]-(:Entity) }} AS connection_count
ORDER BY observation_count DESC, name, _id
LIMIT $limit
"""

_TIME_CYPHER = f"""
MATCH (d:Document)
WHERE {domain_predicate("d")}
RETURN count(d) AS documents, count(d.valid_from) AS with_valid_from,
       min(d.valid_from) AS earliest, max(d.valid_from) AS latest
"""


def _run(cypher: str, params: dict):
    return graph_query._run_read_query(cypher, params)


def _class_descriptions(domain: str) -> dict:
    try:
        from artmind import temporal

        schema = temporal.load_schema(domain) or {}
        types = schema.get("entity_types") or {}
        return {str(k): " ".join(str((v or {}).get("description") or "").split()) for k, v in types.items()}
    except Exception as exc:  # a missing or broken schema must not stop planning
        logger.debug("catalogue: no schema descriptions for {}: {}", domain, exc)
        return {}


def build_classes(domain: str) -> dict:
    descriptions = _class_descriptions(domain)
    return {"classes": tuple(
        (r["name"], int(r["n"]), descriptions.get(r["name"], ""))
        for r in _run(_CLASSES_CYPHER, {"domains": [domain]})
    )}


def build_relations(domain: str) -> dict:
    return {"relations": tuple(
        (r["rel_type"], int(r["n"]))
        for r in _run(_RELATIONS_CYPHER, {"domains": [domain], "limit": RELATIONS_MAX})
    )}


def build_documents(domain: str) -> dict:
    """Every live and retired document name (what the planner reads) plus one record per
    live document: id, domain, valid_from, has_summary."""
    rows = _run(_DOCUMENTS_CYPHER, {"domains": [domain]})
    names = tuple(sorted({r["name"] for r in rows if r.get("name")}))
    records = tuple(sorted(
        (DocRecord(id=str(r["id"]), name=str(r.get("name") or ""), domain=r.get("domain") or "",
                   valid_from=None if r.get("valid_from") is None else str(r["valid_from"]),
                   has_summary=bool(r.get("has_summary")))
         for r in rows if r.get("live") and r.get("id")),
        key=lambda rec: (rec.name, rec.id),
    ))
    return {"documents": names, "doc_records": records}


def build_names(domain: str) -> dict:
    rows = _run(_NAMES_CYPHER, {"domains": [domain], "limit": NAME_INDEX_MAX + 1})
    return {
        "truncated": len(rows) > NAME_INDEX_MAX,
        "names": tuple(
            NameEntry(
                _id=r.get("_id"), name=r["name"],
                aliases=tuple(a for a in (r.get("aliases") or ()) if a),
                entity_class=r.get("entity_class") or "",
                observation_count=int(r.get("observation_count") or 0),
                domain=r.get("domain") or "", connection_count=int(r.get("connection_count") or 0),
            )
            for r in rows[:NAME_INDEX_MAX]
        ),
    }


def build_stores(domain: str) -> dict:
    """Which stores answer for the domain: the graph (or chunks only, when its schema
    sets `graph_extraction: false`) and the structured tables registered at or around it."""
    from artmind.schema_validate import domain_graph_extraction

    graph = domain_graph_extraction(domain)
    try:
        from artmind.structured import registry

        tables = len(registry.list_tables([domain]))
    except Exception as exc:  # no registry yet is a domain without tables
        logger.debug("catalogue: no structured tables for {}: {}", domain, exc)
        tables = 0
    return {"stores": {"graph_extraction": graph, "kind": "graph" if graph else "chunks",
                       "structured_tables": tables}}


def build_time(domain: str) -> dict:
    rows = _run(_TIME_CYPHER, {"domains": [domain]})
    row = rows[0] if rows else {}
    total = int(row.get("documents") or 0)
    dated = int(row.get("with_valid_from") or 0)
    return {"time": {
        "documents": total, "with_valid_from": dated,
        "share": round(dated / total, 4) if total else 0.0,
        "earliest": None if row.get("earliest") is None else str(row["earliest"]),
        "latest": None if row.get("latest") is None else str(row["latest"]),
    }}


#: The parts a builder exists for. `assets` and `health` register theirs when their
#: modules land; a part without a builder is not offered.
BUILDERS: dict[str, Callable[[str], dict]] = {
    "classes": build_classes,
    "relations": build_relations,
    "documents": build_documents,
    "names": build_names,
    "stores": build_stores,
    "time": build_time,
}


def _register_health() -> None:
    from artmind.catalogue import health

    BUILDERS["health"] = health.build_health


_register_health()


def _registry_stamp() -> int:
    try:
        from artmind import db

        return db.DB_PATH.stat().st_mtime_ns
    except (OSError, AttributeError):
        return 0


def part_key(part: str, fingerprint: tuple) -> list:
    """What `part` is built from, as a JSON-stable list.

    Graph parts follow the domain's generation (plus the entity/document/history counts
    that catch a writer outside the choke points); `classes` also its schema files (the
    descriptions), `stores` only the schema files and the registry, `assets` the graph
    and its three disk stores."""
    graph = list(fingerprint[:4])
    disk = fingerprint[4] if len(fingerprint) > 4 else ""
    if part in ("relations", "documents", "names", "time"):
        return graph
    if part == "classes":
        return [*graph, stamps.part(disk, "schemas")]
    if part == "stores":
        return [stamps.part(disk, "schemas"), _registry_stamp()]
    if part == "assets":
        return [*graph, *(stamps.part(disk, p) for p in ("views", "topics", "insights"))]
    if part == "health":
        return ["ttl"]  # follows its TTL only; a changed generation schedules a background refresh
    raise KeyError(part)
