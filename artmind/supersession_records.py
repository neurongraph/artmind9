"""Supersession records -- the `supersessions` curation kind (spec 2026-09-26
§7, D6; §15 A10).

`(:Document)-[:SUPERSEDES {scope}]->(:Document)` is asserted by
`temporal.apply_supersession` -- from `ingest supersede`, `ingest
detect-supersession`, and the adjudicator's "superseded" verdict -- and used
to live only in the graph. Each assertion is now also a file,
`.artmind/data/curation/supersessions/<id>.json`:

    {"id", "newer_doc_id", "older_doc_id", "scope", "effective",
     "detected_by", "domains", "recorded_at"}

The id is sha1 of `newer|older|scope`, so re-asserting the same supersession
on any machine names the same record. `recorded_at` (UTC) is when it was first
asserted, kept from the file on a re-assertion, so re-running detection
rewrites nothing. With no `effective` given, it is the one already in the file,
else the newer document's own `_valid_from` when the document states one (precision other than `none`;
spec 2026-11-15 §6.1, §7.2).

**`older.superseded_by` is derived from the edges**: after each apply or
removal it is the newer document of the `SUPERSEDES` edge with the latest
`effective` (ties by id), null when no edge remains.

**Superseding ends validity** (spec 2026-11-15 §7.2; amendment, decided
2026-11-18). For `scope: document`, the older document and its facts --
observations, chunks, passages and summaries, under either label, every version
-- are closed at `E`, and `_valid_to_by = <record id>` names the record that
closed them. `E` is the edge's `effective`, else the newer document's own
`_valid_from` when it states one (precision other than `none`); with neither,
nothing is closed. Two rules, by what the end means:

- the document-level nodes (Document, DocChunk, DocPassage, DocSummary) carry the
  document's end, and the EARLIER end wins: `_valid_to = E` where it is empty or
  later than `E` (ISO strings compared as strings). Each keeps the document's
  stated end as `_doc_valid_to` (written at commit), and un-asserting restores it:
  `_valid_to = _doc_valid_to` (none when the document states no end);
- an observation's `_valid_to` is its fact's own end, never overwritten: the close
  fills only a missing one, and un-asserting removes only an end a record wrote.

`restate_validity` is the one writer: it reopens every node a supersession
closed (`_valid_to_by` set) and closes again from the `SUPERSEDES` edges that
remain, earliest resolved end first (the edge's `effective`, else the newer document's
stated `_valid_from`; an edge with no end last), so a document superseded
twice ends at its first replacement -- the same result on every machine whatever
order records are applied in. A document commit runs it too
(`ingest._commit_document_tx`): the commit rewrites nodes from staging, which
carries no supersession end, so the end is derived again from the edges in
the same transaction rather than preserved from the node it replaces.

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
SET older.superseded_by = CASE WHEN size(edges) = 0 THEN null ELSE edges[0].newer END
"""

# The nodes a supersession closes, one index-backed statement per (label, key):
# the document itself and everything it asserted, current and history.
VALIDITY_TARGETS = (
    ("Document", "id"), ("DocumentHistory", "id"),
    ("Observation", "doc_id"), ("ObservationHistory", "doc_id"),
    ("DocChunk", "doc_id"), ("DocChunkHistory", "doc_id"),
    ("DocPassage", "doc_id"), ("DocPassageHistory", "doc_id"),
    ("DocSummary", "doc_id"), ("DocSummaryHistory", "doc_id"),
)

# The document-scope edges into the older document, in the order they close: by the end
# each one writes (`e`: its effective, else the newer document's stated `_valid_from`),
# earliest first, ties by newer id, an edge with no end last -- the end is the first
# replacement's, whether its date came from the edge or from the newer document.
_DOCUMENT_EDGES = """
OPTIONAL MATCH (o1:Document {id: $older}) OPTIONAL MATCH (o2:DocumentHistory {id: $older})
WITH coalesce(o1, o2) AS older
WHERE older IS NOT NULL
MATCH (newer)-[s:SUPERSEDES {scope: 'document'}]->(older)
RETURN s.record_id AS record_id, newer.id AS newer,
       coalesce(s.effective, CASE WHEN newer._valid_precision = 'none' THEN null ELSE newer._valid_from END) AS e
ORDER BY e IS NULL, e ASC, newer.id ASC
"""

# The documents `$newer` supersedes through an undated edge (document scope): only their
# ends fall back to its `_valid_from`; a dated edge's end never reads it.
_SUPERSEDED_BY_NEWER = """
OPTIONAL MATCH (n1:Document {id: $newer}) OPTIONAL MATCH (n2:DocumentHistory {id: $newer})
WITH coalesce(n1, n2) AS newer
WHERE newer IS NOT NULL
MATCH (newer)-[s:SUPERSEDES {scope: 'document'}]->(older)
WHERE s.effective IS NULL
RETURN DISTINCT older.id AS older
"""

# The labels whose `_valid_to` is the document's end (min of stated and supersession end,
# restored from `_doc_valid_to`); the rest (observations) carry their fact's own end.
DOCUMENT_LEVEL = frozenset({"Document", "DocumentHistory", "DocChunk", "DocChunkHistory", "DocPassage",
                            "DocPassageHistory", "DocSummary", "DocSummaryHistory"})

_REOPEN_DOCUMENT_LEVEL = """
MATCH (x:{label} {{{key}: $older}}) WHERE x._valid_to_by IS NOT NULL
SET x._valid_to = x._doc_valid_to, x._valid_to_by = null
RETURN count(x) AS n
"""

_REOPEN_FACT = """
MATCH (x:{label} {{{key}: $older}}) WHERE x._valid_to_by IS NOT NULL
REMOVE x._valid_to, x._valid_to_by
RETURN count(x) AS n
"""

_CLOSE_DOCUMENT_LEVEL = """
MATCH (x:{label} {{{key}: $older}}) WHERE x._valid_to IS NULL OR x._valid_to > $effective
SET x._valid_to = $effective, x._valid_to_by = $record_id
RETURN count(x) AS n
"""

_CLOSE_FACT = """
MATCH (x:{label} {{{key}: $older}}) WHERE x._valid_to IS NULL
SET x._valid_to = $effective, x._valid_to_by = $record_id
RETURN count(x) AS n
"""


def _reopen_cypher(label: str, key: str) -> str:
    return (_REOPEN_DOCUMENT_LEVEL if label in DOCUMENT_LEVEL else _REOPEN_FACT).format(label=label, key=key)


def _close_cypher(label: str, key: str) -> str:
    return (_CLOSE_DOCUMENT_LEVEL if label in DOCUMENT_LEVEL else _CLOSE_FACT).format(label=label, key=key)


_NEWER_VALID_FROM = """
MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND d.id = $id
RETURN d._valid_from AS valid_from, d._valid_precision AS precision
LIMIT 1
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
    documents (for `vault sync --domain`), `effective` from the newer
    document when the caller gives none."""
    from artmind import curation_records

    rows = session.run(_DOMAINS, ids=[newer_doc_id, older_doc_id]).data()
    domains = sorted({row.get("domain") for row in rows if isinstance(row, dict) and isinstance(row.get("domain"), str)})
    rid = record_id(newer_doc_id, older_doc_id, scope)
    if effective is None:
        # Kept from the file once derived, so a later change to the newer document's
        # date does not rewrite a re-asserted record.
        effective = curation_records.first_value(NAME, rid, "effective", _stated_valid_from(session, newer_doc_id))
    return {
        "id": rid,
        "newer_doc_id": newer_doc_id,
        "older_doc_id": older_doc_id,
        "scope": scope,
        "effective": effective,
        "detected_by": detected_by,
        "domains": domains,
        "recorded_at": curation_records.recorded_at(NAME, rid),
    }


def _stated_valid_from(session, doc_id: str) -> str | None:
    """The document's `_valid_from` when its source states it; None for a
    default date (precision `none`) or none at all."""
    row = session.run(_NEWER_VALID_FROM, id=doc_id).single()
    if row is None:
        return None
    value, precision = row.get("valid_from"), row.get("precision")
    return value if isinstance(value, str) and value and precision != "none" else None


def _run_count(tx, cypher: str, **params) -> int:
    row = tx.run(cypher, **params).single()
    try:
        return int((row or {}).get("n") or 0)
    except (TypeError, ValueError):
        return 0


def reopen_validity(tx, older: str) -> int:
    """Undo every supersession end on the older document's nodes (`_valid_to_by` set): a
    document-level node gets its document's stated end back (`_doc_valid_to`, none when it
    states none), an observation loses the end. A node with no marker is never touched."""
    return sum(_run_count(tx, _reopen_cypher(label, key), older=older) for label, key in VALIDITY_TARGETS)


def close_validity(tx, older: str, effective: str, record_id: str) -> int:
    """`_valid_to = effective`, marked `_valid_to_by = record_id`: on the older document's
    document-level nodes with no end or a later one, on its observations with no end."""
    return sum(_run_count(tx, _close_cypher(label, key), older=older, effective=effective,
                          record_id=record_id)
               for label, key in VALIDITY_TARGETS)


def restate_validity(tx, older: str) -> int:
    """Derive the older document's supersession ends from its document-scope
    `SUPERSEDES` edges: reopen what any supersession closed, then close from
    each edge with an end date, earliest first (a node keeps the first replacement's end;
    a document-level node the earlier of that and its stated end). Returns how many nodes
    are closed."""
    edges = [row for row in tx.run(_DOCUMENT_EDGES, older=older).data()
             if isinstance(row, dict) and row.get("record_id")]
    reopen_validity(tx, older)
    closed = 0
    for edge in edges:
        if isinstance(edge.get("e"), str) and edge["e"]:
            closed += close_validity(tx, older, edge["e"], edge["record_id"])
    return closed


def count_validity(session, older: str, *, closed_by: str | None = None, effective: str | None = None) -> int:
    """Read-only, for the `effect` blocks: the older document's nodes a
    supersession would end (no end yet, or one a supersession wrote, which
    `restate_validity` writes again; with `effective`, also a document-level node whose
    end is later) -- or, with `closed_by`, the nodes that record closed."""
    total = 0
    for label, key in VALIDITY_TARGETS:
        params: dict = {"older": older}
        if closed_by:
            where, params["record_id"] = "x._valid_to_by = $record_id", closed_by
        elif effective and label in DOCUMENT_LEVEL:
            where = "(x._valid_to IS NULL OR x._valid_to_by IS NOT NULL OR x._valid_to > $effective)"
            params["effective"] = effective
        else:
            where = "(x._valid_to IS NULL OR x._valid_to_by IS NOT NULL)"
        total += _run_count(session, f"MATCH (x:{label} {{{key}: $older}}) WHERE {where} RETURN count(x) AS n",
                            **params)
    return total


def restate_superseded(tx, newer: str) -> list[str]:
    """`restate_validity` for every document `newer` supersedes (a commit of
    `newer` may have changed the `_valid_from` their ends fall back to).
    Returns those documents' ids."""
    olders = sorted({row["older"] for row in tx.run(_SUPERSEDED_BY_NEWER, newer=newer).data()
                     if isinstance(row, dict) and row.get("older")})
    for older in olders:
        restate_validity(tx, older)
    return olders


def apply(tx, record: dict, fingerprint: str) -> set:
    """MERGE the `SUPERSEDES` edge (both documents must exist, live or
    retired), bump the older document's catalogue generation, recompute the older document's `superseded_by` from its edges
    and, for `scope: document`, its ends of validity (`restate_validity`).
    Returns the older document's aggregate keys: its observations' `_valid_to`
    may have changed, so the caller rebuilds them."""
    from artmind import projection

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
    from artmind.catalogue import state as catalogue_state

    tx.run(_RECOMPUTE, older=record["older_doc_id"])
    # `superseded_by` and the ends are catalogue reads (spec 2026-11-02 §3).
    catalogue_state.bump_for_document(tx, record["older_doc_id"])
    if record["scope"] != "document":
        return set()
    restate_validity(tx, record["older_doc_id"])
    return set(projection.keys_for_document(tx, record["older_doc_id"]))


def remove(tx, record: dict) -> set:
    """Un-assert: delete the edge the record names, then recompute the older document's `superseded_by` and ends of
    validity from the edges that remain. A record whose content could not be
    read (only its id is known, or it lacks a newer/older/scope field) matches
    the edge by `record_id` instead, and every older document that edge
    pointed at is recomputed. Returns the older documents' aggregate keys."""
    from artmind import projection

    if record.get("newer_doc_id") and record.get("older_doc_id") and record.get("scope"):
        tx.run(_REMOVE, newer=record["newer_doc_id"], older=record["older_doc_id"], scope=record["scope"])
        olders = [record["older_doc_id"]]
    else:
        rows = tx.run(_REMOVE_BY_ID, id=record["id"]).data()
        olders = sorted({row["oid"] for row in rows if row.get("oid")})
    from artmind.catalogue import state as catalogue_state

    keys: set = set()
    for older in olders:
        tx.run(_RECOMPUTE, older=older)
        catalogue_state.bump_for_document(tx, older)
        # Reopening everything and closing again from the remaining edges reopens
        # exactly what this record closed (or hands it to another supersession).
        restate_validity(tx, older)
        keys |= set(projection.keys_for_document(tx, older))
    return keys


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
