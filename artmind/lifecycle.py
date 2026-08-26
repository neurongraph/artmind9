"""Retire and restore — moving a document's assertions along the **assertion
time** axis.

Retiring is an assertion-time act with no date semantics: it moves a document
and everything it asserted from `latest` to `history`. A retired document's
facts keep the valid-time window they always had. Confusing the two axes is
the most common modelling error in this system, so nothing here writes a date.

This is the primitive that finally makes entity retirement work. The old code
had *three* mechanisms trying to decide which entities a superseded document
should take with it — `_retire_orphaned_entities`, the `size(docIds) = 1`
heuristic, and a scoped GC — and all three failed silently, leaving 235
entities live whose only source was a superseded document. There is now one
rule, and it is not a heuristic: **an aggregate key with zero `latest`
observations has its `:Entity` deleted.** Retire simply demotes observations
and lets the projection work it out.

Phase 5 owns the rest of the lifecycle surface (`archive`,
`restore-from-archive`, `archived`). Retire and restore land here because
Phase 3 needs them: with observations in place, a superseded document whose
observations stayed `latest` would keep its entities alive — reintroducing the
exact defect the projection removes.
"""
from __future__ import annotations

from loguru import logger

from artmind.graph_query import neo4j_session


def _transition(tx, doc_id: str, *, to_history: bool) -> dict:
    """Move one document's node, chunks and observations between the base
    label and its History counterpart, then rebuild the keys it touched.
    Runs in the caller's transaction.

    A **label swap**, not a status property — there is no `_status` left on
    these nodes. `retire`/`restore` also mean "leave"/"return to"
    `chunk_text_ft` and `chunk_embedding`, both of which are defined only
    `FOR (c:DocChunk)`: swapping the label is what structurally moves a
    document's chunks in and out of those indexes, with no predicate anywhere
    that could be forgotten.
    """
    from artmind import projection

    # Captured BEFORE the transition, spanning both labels: these are the
    # keys whose aggregates change, whichever direction we are moving.
    keys = projection.keys_for_document(tx, doc_id)

    if to_history:
        from_obs, to_obs = "Observation", "ObservationHistory"
        from_doc, to_doc = "Document", "DocumentHistory"
        from_chunk, to_chunk = "DocChunk", "DocChunkHistory"
    else:
        from_obs, to_obs = "ObservationHistory", "Observation"
        from_doc, to_doc = "DocumentHistory", "Document"
        from_chunk, to_chunk = "DocChunkHistory", "DocChunk"

    observations = tx.run(
        f"""
        MATCH (o:{from_obs} {{doc_id: $doc_id}})
        REMOVE o:{from_obs} SET o:{to_obs}
        RETURN count(o) AS n
        """,
        doc_id=doc_id,
    ).single()

    tx.run(
        f"MATCH (d:{from_doc} {{id: $doc_id}}) REMOVE d:{from_doc} SET d:{to_doc}",
        doc_id=doc_id,
    )
    tx.run(
        f"MATCH (c:{from_chunk} {{doc_id: $doc_id}}) REMOVE c:{from_chunk} SET c:{to_chunk}",
        doc_id=doc_id,
    )

    summary = projection.rebuild(tx, keys, synthesis_loader=lambda k: projection.load_synthesis(tx, k))
    return {
        "doc_id": doc_id,
        "observations": int(observations["n"]) if observations else 0,
        "keys": sorted(keys),
        "projection": summary,
    }


def retire_document(doc_id: str, domain: str | None = None) -> dict:
    """Move a document and everything it asserted from `latest` to `history`.

    The document and its observations stay in storage and stay reachable by
    asking for them; they leave every index. Entities left with no `latest`
    observation anywhere are deleted by the rebuild — not because this function
    decided they were orphans, but because nothing asserts them any more.
    """
    with neo4j_session() as session:
        result = session.execute_write(_transition, doc_id, to_history=True)
    logger.info(
        "Retired {}: {} observation(s) → history, projection {}",
        doc_id, result["observations"], result["projection"],
    )
    if domain:
        _sweep(domain, result["keys"])
    return result


def restore_document(doc_id: str, domain: str | None = None) -> dict:
    """Move a document's assertions back from `history` to `latest`.

    The exact inverse. Because ids are deterministic and the projection is
    derived, restoring recreates the same entities with the same ids rather
    than a parallel set.
    """
    with neo4j_session() as session:
        result = session.execute_write(_transition, doc_id, to_history=False)
    logger.info(
        "Restored {}: {} observation(s) → latest, projection {}",
        doc_id, result["observations"], result["projection"],
    )
    if domain:
        _sweep(domain, result["keys"])
    return result


def _sweep(domain: str, keys: list) -> int:
    from artmind.ingest import _sweep_embeddings

    return _sweep_embeddings(domain, keys)


def resolve_document_id(name_or_id: str, domain: str | None = None) -> str | None:
    """Find a document by id or by name — nobody should have to type a uuid.

    Matches `(d:Document OR d:DocumentHistory)`, not `:Document` alone — a
    document `restore-from-archive` just placed in history (Phase 5's exact
    end state) needs to resolve here too, or `docs restore --documentName
    <anything, even the exact id>` cannot find it, which is exactly the
    document a human would want to promote next. Found live in Phase 5,
    fixed here (Phase 6) since nothing else touches this function in between.
    """
    from artmind.graph_query import read_session

    clause = " AND (d.domain = $domain OR d.domain STARTS WITH ($domain + '.'))" if domain else ""
    with read_session() as session:
        rows = session.run(
            f"""
            MATCH (d) WHERE (d:Document OR d:DocumentHistory)
            AND (d.id = $ref OR toUpper(d.name) = toUpper($ref)
                 OR toUpper(coalesce(d.title, '')) = toUpper($ref)){clause}
            RETURN d.id AS id, d.name AS name
            """,
            ref=name_or_id, **({"domain": domain} if domain else {}),
        ).data()
    if not rows:
        return None
    if len(rows) > 1:
        logger.warning(
            "{!r} matches {} documents ({}); using the first",
            name_or_id, len(rows), ", ".join(r["name"] for r in rows[:3]),
        )
    return rows[0]["id"]
