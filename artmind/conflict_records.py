"""Conflict records -- the `conflicts` curation kind (spec 2026-09-26 §14 A6).

A cross-domain conflict is detected by an LLM (`conflicts.materialize`) and
closed by a human (`conflicts.resolve_conflict`). Both used to live only in
the graph, so a machine with its own Neo4j never saw either. Each conflict is
now also a file, `.artmind/data/curation/conflicts/<id>.json`, and this module
owns the record's shape and every Cypher statement that writes it to (or
removes it from) the graph -- the one path shared by detection, resolution
and `vault sync`.

A record:

    {"id", "verdict", "aspect", "claim_a", "claim_b", "severity",
     "entity_class", "entities": [{"side", "id", "key", "name", "domain"}, x2],
     "domains", "evidence": [{"side", "chunk_id", "doc_id"}],
     "status", "resolution_reason", "resolved_at",
     "detected_at", "detected_by_model", "source": "adjudicator"}

The id is `conflicts.conflict_id` -- sha1 of the two entity ids (sorted) and
the aspect -- and entity ids are sha256 of `name|class|domain`, so two
machines detecting the same conflict name the same record.

Only the adjudicator's conflicts are records. The projection's own
`:Conflict {_source: 'projection'}` nodes are derived from observations and
rebuilt by every projection rebuild; they need no file.
"""
from __future__ import annotations

from artmind.curation_records import Kind

NAME = "conflicts"

# `apply` is four statements, not one: the node and its fields; the edges
# (which report whether both entities matched); the evidence (which reports how
# many chunks matched); and only then the fingerprint. A fingerprint on a node
# that is missing an edge or an evidence link would make the next sync skip the
# record as "already applied" and leave the conflict incomplete forever.
_APPLY_NODE = """
MERGE (co:Conflict {id: $id})
SET co._source = 'adjudicator',
    co.verdict = $verdict, co.aspect = $aspect,
    co.claim_a = $claim_a, co.claim_b = $claim_b, co.severity = $severity,
    co.entity_class = $entity_class, co.domains = $domains,
    co.status = $status, co.resolution_reason = $resolution_reason, co.resolved_at = $resolved_at,
    co.detected_at = $detected_at, co.detected_by_model = $detected_by_model
"""

# A MATCH on a missing entity yields no row, so `n` is 1 exactly when BOTH matched.
_APPLY_EDGES = """
MATCH (co:Conflict {id: $id})
MATCH (a:Entity {_id: $idA}), (b:Entity {_id: $idB})
MERGE (co)-[:CONFLICT_OF]->(a)
MERGE (co)-[:CONFLICT_OF]->(b)
MERGE (a)-[ra:CONFLICTS_WITH]->(b) SET ra.conflict_id = $id, ra.aspect = $aspect
MERGE (b)-[rb:CONFLICTS_WITH]->(a) SET rb.conflict_id = $id, rb.aspect = $aspect
RETURN count(*) AS n
"""

_APPLY_EVIDENCE = """
UNWIND $evidence AS ev
MATCH (co:Conflict {id: $id}), (c:DocChunk {id: ev.chunk_id})
MERGE (co)-[:EVIDENCE {side: ev.side}]->(c)
RETURN count(DISTINCT c.id) AS n
"""

_APPLY_FINGERPRINT = "MATCH (co:Conflict {id: $id}) SET co.record_fingerprint = $fingerprint"

_REMOVE_EDGES = "MATCH (:Entity)-[r:CONFLICTS_WITH {conflict_id: $id}]->(:Entity) DELETE r"
_REMOVE_NODE = "MATCH (co:Conflict {id: $id, _source: 'adjudicator'}) DETACH DELETE co"

_READ_FINGERPRINTS = (
    "MATCH (co:Conflict) WHERE co.id IN $ids "
    "RETURN co.id AS id, co.record_fingerprint AS fingerprint"
)

_FROM_GRAPH = """
MATCH (co:Conflict {id: $id})
WHERE coalesce(co._source, 'adjudicator') = 'adjudicator'
OPTIONAL MATCH (co)-[:CONFLICT_OF]->(e:Entity)
WITH co, collect(DISTINCT {id: e._id, key: e.key, name: e.name, domain: e._domain}) AS entities
OPTIONAL MATCH (co)-[ev:EVIDENCE]->(c:DocChunk)
RETURN properties(co) AS co, entities,
       collect(DISTINCT {side: ev.side, chunk_id: c.id, doc_id: c.doc_id}) AS evidence
"""


def record_from_detection(
    conflict_id: str, pair: dict, verdict: dict, evidence_a: list[dict], evidence_b: list[dict],
    model: str, detected_at: str,
) -> dict:
    """A fresh record for a `conflicting_claims` verdict, status open."""
    return {
        "id": conflict_id,
        "verdict": verdict["verdict"],
        "aspect": verdict.get("aspect") or "",
        "claim_a": verdict.get("claim_a") or "",
        "claim_b": verdict.get("claim_b") or "",
        "severity": verdict.get("severity") or "low",
        "entity_class": pair.get("entity_class"),
        "entities": [
            {"side": side, "id": pair[f"id_{side}"], "key": pair.get(f"key_{side}"),
             "name": pair.get(f"name_{side}"), "domain": pair.get(f"domain_{side}")}
            for side in ("a", "b")
        ],
        "domains": sorted({pair["domain_a"], pair["domain_b"]}),
        "evidence": [
            {"side": side, "chunk_id": c["id"], "doc_id": c.get("doc_id")}
            for side, chunks in (("a", evidence_a), ("b", evidence_b)) for c in chunks
        ],
        "status": "open",
        "resolution_reason": None,
        "resolved_at": None,
        "detected_at": detected_at,
        "detected_by_model": model,
        "source": "adjudicator",
    }


def record_from_graph(session, conflict_id: str) -> dict | None:
    """The record an adjudicator `:Conflict` node that predates its file
    would have had -- read back from the graph, so resolving (or re-detecting)
    an old conflict writes its file for the first time. None when there is no
    such node. The graph does not know which entity was side `a`, so sides
    follow entity-id order."""
    rec = session.run(_FROM_GRAPH, id=conflict_id).single()
    node = rec.get("co") if rec else None
    if not node:
        return None
    entities = sorted((e for e in rec.get("entities") or [] if e.get("id")), key=lambda e: e["id"])
    return {
        "id": conflict_id,
        "verdict": node.get("verdict") or "conflicting_claims",
        "aspect": node.get("aspect") or "",
        "claim_a": node.get("claim_a") or "",
        "claim_b": node.get("claim_b") or "",
        "severity": node.get("severity") or "low",
        "entity_class": node.get("entity_class"),
        "entities": [
            {"side": side, "id": e["id"], "key": e.get("key"), "name": e.get("name"), "domain": e.get("domain")}
            for side, e in zip(("a", "b"), entities)
        ],
        "domains": sorted(node.get("domains") or {e.get("domain") for e in entities if e.get("domain")}),
        "evidence": sorted(
            ({"side": ev.get("side"), "chunk_id": ev["chunk_id"], "doc_id": ev.get("doc_id")}
             for ev in rec.get("evidence") or [] if ev.get("chunk_id")),
            key=lambda ev: (ev["side"] or "", ev["chunk_id"]),
        ),
        "status": node.get("status") or "open",
        "resolution_reason": node.get("resolution_reason"),
        "resolved_at": node.get("resolved_at"),
        "detected_at": node.get("detected_at"),
        "detected_by_model": node.get("detected_by_model"),
        "source": "adjudicator",
    }


def _count(result) -> int:
    row = result.single()
    return int(row["n"]) if row and row["n"] is not None else 0


def apply(tx, record: dict, fingerprint: str) -> set:
    """MERGE the `:Conflict` node from `record` -- every field SET, so the
    record, not whatever the graph held, is the truth -- plus its
    `CONFLICT_OF`/`CONFLICTS_WITH` edges and its `EVIDENCE` edges. Idempotent.
    Changes no projection, so returns no keys.

    The record's fingerprint is written LAST and only when the record is
    complete: both entities matched and every evidence chunk matched. An
    incomplete apply (an entity or chunk this graph has not replayed yet, or
    an id a same-as merge changed) leaves the node without a fingerprint that
    matches the file, so the next sync sees the record as changed and applies
    it again -- healing once the missing pieces arrive; a target that never
    arrives stays honestly pending."""
    entities = record.get("entities") or []
    id_a = entities[0]["id"] if len(entities) > 0 else None
    id_b = entities[1]["id"] if len(entities) > 1 else None
    tx.run(
        _APPLY_NODE,
        id=record["id"], verdict=record.get("verdict"), aspect=record.get("aspect"),
        claim_a=record.get("claim_a"), claim_b=record.get("claim_b"), severity=record.get("severity"),
        entity_class=record.get("entity_class"), domains=list(record.get("domains") or []),
        status=record.get("status") or "open", resolution_reason=record.get("resolution_reason"),
        resolved_at=record.get("resolved_at"), detected_at=record.get("detected_at"),
        detected_by_model=record.get("detected_by_model"),
    )
    entities_matched = _count(tx.run(
        _APPLY_EDGES, id=record["id"], aspect=record.get("aspect"), idA=id_a, idB=id_b,
    )) >= 1
    evidence = [
        {"side": ev.get("side"), "chunk_id": ev["chunk_id"]}
        for ev in record.get("evidence") or [] if ev.get("chunk_id")
    ]
    evidence_matched = True
    if evidence:
        wanted = len({ev["chunk_id"] for ev in evidence})
        evidence_matched = _count(tx.run(_APPLY_EVIDENCE, id=record["id"], evidence=evidence)) >= wanted
    if entities_matched and evidence_matched:
        tx.run(_APPLY_FINGERPRINT, id=record["id"], fingerprint=fingerprint)
    return set()


def remove(tx, record: dict) -> set:
    """Undo a record whose file was deleted: its `CONFLICTS_WITH` edges and
    the adjudicator `:Conflict` node (with its `CONFLICT_OF`/`EVIDENCE`
    edges). A projection conflict is never touched."""
    tx.run(_REMOVE_EDGES, id=record["id"])
    tx.run(_REMOVE_NODE, id=record["id"])
    return set()


def read_fingerprints(ids: list[str], *, timeout: float | None = None) -> dict:
    """`{id: record_fingerprint}` for every id the graph holds a `:Conflict`
    for, in ONE query. A conflict detected before records existed maps to
    None."""
    from artmind import graph_query

    if not ids:
        return {}
    with graph_query.neo4j_session(timeout=timeout) as session:
        rows = session.run(_READ_FINGERPRINTS, ids=sorted(set(ids))).data()
    return {row["id"]: row["fingerprint"] for row in rows}


def domains(record: dict) -> list:
    return list(record.get("domains") or [])


KIND = Kind(name=NAME, phase="post", apply=apply, remove=remove, read_fingerprints=read_fingerprints, domains=domains)
