"""The entity history zone: snapshots of overwritten entity property values.

Document supersession retires entities wholesale (see temporal._retire_orphaned_entities).
This module handles the other half: when a superseding document *overwrites* an
entity's property values rather than dropping the entity, the prior values are
preserved as an :EntityVersion node so point-in-time questions stay answerable.

Snapshots deliberately carry neither the :Entity label nor a class label. Every
existing consumer — pattern1-9, entity_listing, entity-resolve, the
entity_embedding vector index, refine-graph clustering, candidate_pairs — matches
on :Entity or a class label, so none can see history without asking. That
isolation is structural, not a filter anyone has to remember.
"""
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from artmind.graph_query import neo4j_session
from artmind.temporal import (
    _read_doc_body,
    load_schema,
    parse_supersession_metadata_table,
    parse_supersession_notice,
)


def supersession_possible(doc_name: str, domain: str) -> bool:
    """Could supersession fire for this document? Pure local work.

    The parse step needs no graph access — both parsers are regex over markdown
    already on disk — so this runs before the (much more expensive) prior-value
    capture and skips it entirely for the overwhelming majority of documents,
    which declare no supersession at all.

    The title-family route is the one signal that lives outside the document, so
    a domain with `supersede_on_title_family` set always passes the gate. That
    flag is off by default and set only by schema authors who want version
    chains, so those domains genuinely expect supersession.
    """
    defaults = (load_schema(domain).get("temporal") or {}).get("defaults") or {}
    if defaults.get("supersede_on_title_family"):
        return True
    body = _read_doc_body(doc_name)
    if not body:
        return False
    return bool(parse_supersession_notice(body) or parse_supersession_metadata_table(body))


_CAPTURE_CYPHER = """
UNWIND $rows AS r
MATCH (n:Entity {name: r.name, entity_class: r.ec, domain: r.domain})
RETURN r.idx AS idx, n.id AS id, n.valid_from AS vf, [k IN r.keys | [k, n[k]]] AS prior
"""


def _staged_assertions(doc_kg_dir: Path, domain: str) -> list[dict]:
    """The (identity, asserted property keys) pairs this document will write.

    entities.json ids are chunk-scoped — the same logical entity mentioned in
    multiple chunks appears as multiple entries sharing one (name, entity_class,
    domain) identity but different chunk-scoped ids and different property
    subsets. Group by identity and union the keys, or a later chunk's row would
    silently overwrite an earlier chunk's captured values for the same entity
    (and the batched Cypher query would carry redundant MATCH lookups for one
    node). Emit exactly one row per unique identity.

    Mirrors _reassert_superseding_properties' own scope: only the domain
    properties from properties.json. name/description/aliases/context stay
    accretive — that is consolidation's job, not history's.
    """
    try:
        entities = json.loads((doc_kg_dir / "entities.json").read_text(encoding="utf-8"))
        properties_path = doc_kg_dir / "properties.json"
        properties_list = (
            json.loads(properties_path.read_text(encoding="utf-8"))
            if properties_path.exists() else []
        )
    except Exception as e:
        logger.warning("entity_history: could not load staged JSON from {}: {}", doc_kg_dir, e)
        return []

    props_by_id = {p["id"]: p.get("properties", {}) for p in properties_list}
    keys_by_identity: dict[tuple, set] = {}
    for e in entities:
        keys = {k for k, v in props_by_id.get(e["id"], {}).items() if v not in (None, "", [])}
        if not keys:
            continue
        identity = (e["name"], e["entity_class"], e.get("domain") or domain)
        keys_by_identity.setdefault(identity, set()).update(keys)

    rows: list[dict] = []
    for idx, ((name, ec, dom), keys) in enumerate(sorted(keys_by_identity.items())):
        rows.append({"idx": idx, "name": name, "ec": ec, "domain": dom, "keys": sorted(keys)})
    return rows


def capture_prior_values(doc_kg_dir: Path, domain: str) -> dict:
    """Read the live values of exactly the keys this document is about to assert.

    Must run BEFORE write_to_graph(). _upsert_entity's merge is accretive — two
    documents asserting the same string property produce "old | new" — so
    capturing after the write would record the concatenation rather than the
    clean prior value.

    Returns {(name, entity_class, domain): {entity_id, valid_from, values}}.
    An entity with no pre-write node is simply absent: nothing to preserve.
    """
    rows = _staged_assertions(doc_kg_dir, domain)
    if not rows:
        return {}
    with neo4j_session() as session:
        records = session.run(_CAPTURE_CYPHER, rows=rows).data()

    by_idx = {r["idx"]: r for r in rows}
    out: dict = {}
    for rec in records:
        row = by_idx.get(rec["idx"])
        if not row:
            continue
        out[(row["name"], row["ec"], row["domain"])] = {
            "entity_id": rec["id"],
            "valid_from": rec["vf"],
            "values": {k: v for k, v in (rec["prior"] or []) if v is not None},
        }
    return out
