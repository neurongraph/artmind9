"""Supersession records -- the `supersessions` curation kind (spec 2026-09-26
§7, D6; §15 A10).

`(:Document)-[:SUPERSEDES {scope}]->(:Document)` is asserted by
`temporal.apply_supersession` -- from `ingest supersede`, `ingest
detect-supersession`, and the adjudicator's "superseded" verdict -- and used
to live only in the graph. Each assertion is now also a file,
`.artmind/data/curation/supersessions/<id>.json`:

    {"id", "newer_doc_id", "older_doc_id", "scope", "effective",
     "detected_by", "domains"}

The id is sha1 of `newer|older|scope`, so re-asserting the same supersession
on any machine names the same record. No timestamp: re-running detection
rewrites nothing.

**`older.valid_to` and `older.superseded_by` are derived from the edges.**
Only supersession writes them (a document's own window is `_valid_from`/
`_valid_to`, from its staging folder), so both are recomputed from every
`SUPERSEDES` edge still pointing at the older document after each apply or
removal: `superseded_by` is the newer document of the edge with the latest
`effective` (ties by id), `valid_to` that latest non-null `effective`, both
null when no edge remains. Un-asserting a supersession therefore restores
exactly what the remaining assertions say -- no snapshot of an earlier value
to go stale, and the same result on every machine whatever order records are
applied in.

Superseding with `scope: document` also retires the older document -- a
separate curation record (`lifecycle`), which un-asserting the supersession
does not remove: `docs restore` does.
"""
from __future__ import annotations

import hashlib

from loguru import logger

from artmind.curation_records import Kind, matched_count

NAME = "supersessions"

# Both documents, whether live or retired: a `lifecycle` record may already
# have moved the older one to :DocumentHistory.
_BOTH_DOCS = """
OPTIONAL MATCH (n1:Document {id: $newer}) OPTIONAL MATCH (n2:DocumentHistory {id: $newer})
WITH coalesce(n1, n2) AS newer
OPTIONAL MATCH (o1:Document {id: $older}) OPTIONAL MATCH (o2:DocumentHistory {id: $older})
WITH newer, coalesce(o1, o2) AS older
WHERE newer IS NOT NULL AND older IS NOT NULL
"""

_APPLY = _BOTH_DOCS + """
MERGE (newer)-[s:SUPERSEDES {scope: $scope}]->(older)
SET s.effective = $effective, s.detected_by = $detected_by,
    s.record_id = $id, s.record_fingerprint = $fingerprint
RETURN count(s) AS n
"""

_REMOVE = _BOTH_DOCS + """
MATCH (newer)-[s:SUPERSEDES {scope: $scope}]->(older)
DELETE s
"""

# A record whose content could not be read: only its id is known, so the edge
# is matched by `record_id` and the older document it pointed at is returned
# for the recompute.
_REMOVE_BY_ID = """
MATCH ()-[s:SUPERSEDES {record_id: $id}]->(o)
WITH s, o.id AS oid
DELETE s
RETURN oid
"""

_RECOMPUTE = """
OPTIONAL MATCH (o1:Document {id: $older}) OPTIONAL MATCH (o2:DocumentHistory {id: $older})
WITH coalesce(o1, o2) AS older
WHERE older IS NOT NULL
OPTIONAL MATCH (newer)-[s:SUPERSEDES]->(older)
WITH older, newer, s
ORDER BY coalesce(s.effective, '') DESC, newer.id DESC
WITH older, collect(CASE WHEN s IS NULL THEN null ELSE {newer: newer.id, effective: s.effective} END) AS edges
SET older.superseded_by = CASE WHEN size(edges) = 0 THEN null ELSE edges[0].newer END,
    older.valid_to = head([e IN edges WHERE e.effective IS NOT NULL | e.effective])
"""

_DOMAINS = """
MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND d.id IN $ids
RETURN DISTINCT d._domain AS domain
"""

_READ_FINGERPRINTS = (
    "MATCH ()-[s:SUPERSEDES]->() WHERE s.record_id IN $ids "
    "RETURN s.record_id AS id, s.record_fingerprint AS fingerprint"
)


def record_id(newer_doc_id: str, older_doc_id: str, scope: str) -> str:
    return hashlib.sha1(f"{newer_doc_id}|{older_doc_id}|{scope}".encode("utf-8")).hexdigest()


def record_for(
    session, newer_doc_id: str, older_doc_id: str, scope: str, effective: str | None, detected_by: str
) -> dict:
    """The record for one assertion; its `domains` read from the two
    documents (for `vault sync --domain`)."""
    rows = session.run(_DOMAINS, ids=[newer_doc_id, older_doc_id]).data()
    domains = sorted({row.get("domain") for row in rows if isinstance(row, dict) and isinstance(row.get("domain"), str)})
    return {
        "id": record_id(newer_doc_id, older_doc_id, scope),
        "newer_doc_id": newer_doc_id,
        "older_doc_id": older_doc_id,
        "scope": scope,
        "effective": effective,
        "detected_by": detected_by,
        "domains": domains,
    }


def apply(tx, record: dict, fingerprint: str) -> set:
    """MERGE the `SUPERSEDES` edge (both documents must exist, live or
    retired), then recompute the older document's `superseded_by`/`valid_to`
    from its edges. Document-level: returns no projection keys."""
    merged = tx.run(
        _APPLY, newer=record["newer_doc_id"], older=record["older_doc_id"], scope=record["scope"],
        effective=record.get("effective"), detected_by=record.get("detected_by"),
        id=record["id"], fingerprint=fingerprint,
    )
    if matched_count(merged) == 0:
        # Not an error -- the documents may simply not have reached this
        # graph yet, and the record is applied again by a later `vault sync`
        # that has them -- but the edge was not written and nothing was stamped.
        logger.warning(
            "the supersessions record {} was not applied: the newer document {} or the older "
            "document {} is not in this graph (neither live nor retired), so no SUPERSEDES edge was written",
            record["id"], record["newer_doc_id"], record["older_doc_id"],
        )
    tx.run(_RECOMPUTE, older=record["older_doc_id"])
    return set()


def remove(tx, record: dict) -> set:
    """Un-assert: delete the edge the record names, then recompute the older
    document's `superseded_by`/`valid_to` from the edges that remain. A
    record whose content could not be read (only its id is known, or it lacks
    a newer/older/scope field) matches the edge by `record_id` instead, and
    every older document that edge pointed at is recomputed."""
    if record.get("newer_doc_id") and record.get("older_doc_id") and record.get("scope"):
        tx.run(_REMOVE, newer=record["newer_doc_id"], older=record["older_doc_id"], scope=record["scope"])
        tx.run(_RECOMPUTE, older=record["older_doc_id"])
    else:
        rows = tx.run(_REMOVE_BY_ID, id=record["id"]).data()
        for older in sorted({row["oid"] for row in rows if row.get("oid")}):
            tx.run(_RECOMPUTE, older=older)
    return set()


def read_fingerprints(ids: list[str], *, timeout: float | None = None) -> dict:
    from artmind import graph_query

    if not ids:
        return {}
    with graph_query.neo4j_session(timeout=timeout) as session:
        rows = session.run(_READ_FINGERPRINTS, ids=sorted(set(ids))).data()
    return {row["id"]: row["fingerprint"] for row in rows}


def domains(record: dict) -> list:
    return list(record.get("domains") or [])


KIND = Kind(name=NAME, phase="pre", apply=apply, remove=remove, read_fingerprints=read_fingerprints, domains=domains)
