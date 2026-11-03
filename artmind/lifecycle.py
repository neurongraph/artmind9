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


def _transition(tx, doc_id: str, *, to_history: bool, rebuild: bool = True) -> dict:
    """Move one document's node, chunks and observations between the base
    label and its History counterpart, then rebuild the keys it touched --
    unless `rebuild=False`, for a caller that rebuilds them itself (a
    document commit, `vault sync`'s union rebuild). Runs in the caller's
    transaction.

    A **label swap**, not a status property — there is no `_status` left on
    these nodes. `retire`/`restore` also mean "leave"/"return to"
    `passage_text_ft` and `passage_embedding`, both defined only
    `FOR (p:DocPassage)`: swapping the label is what structurally moves a
    document's chunks, passages and summaries in and out of those indexes,
    with no predicate anywhere that could be forgotten. A passage keeps its
    vector under the History label (outside every index), so a restore needs
    no sweep.
    """
    from artmind import projection
    from artmind.catalogue import state as catalogue_state

    # Captured BEFORE the transition, spanning both labels: these are the
    # keys whose aggregates change, whichever direction we are moving.
    keys = projection.keys_for_document(tx, doc_id)

    if to_history:
        from_obs, to_obs = "Observation", "ObservationHistory"
        from_doc, to_doc = "Document", "DocumentHistory"
        from_chunk, to_chunk = "DocChunk", "DocChunkHistory"
        from_passage, to_passage = "DocPassage", "DocPassageHistory"
        from_summary, to_summary = "DocSummary", "DocSummaryHistory"
    else:
        from_obs, to_obs = "ObservationHistory", "Observation"
        from_doc, to_doc = "DocumentHistory", "Document"
        from_chunk, to_chunk = "DocChunkHistory", "DocChunk"
        from_passage, to_passage = "DocPassageHistory", "DocPassage"
        from_summary, to_summary = "DocSummaryHistory", "DocSummary"

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
    tx.run(
        f"MATCH (p:{from_passage} {{doc_id: $doc_id}}) REMOVE p:{from_passage} SET p:{to_passage}",
        doc_id=doc_id,
    )
    tx.run(
        f"MATCH (s:{from_summary} {{doc_id: $doc_id}}) REMOVE s:{from_summary} SET s:{to_summary}",
        doc_id=doc_id,
    )

    # The catalogue's change counter, same transaction (spec 2026-11-02 §3).
    catalogue_state.bump_for_document(tx, doc_id)

    summary = (
        projection.rebuild(tx, keys, synthesis_loader=lambda ks: projection.load_synthesis_batch(tx, ks))
        if rebuild else None
    )
    return {
        "doc_id": doc_id,
        "observations": int(observations["n"]) if observations else 0,
        "keys": sorted(keys),
        "projection": summary,
    }


def plan_transition(doc_id: str, *, to_history: bool) -> dict:
    """Read-only: what retiring (`to_history`) or restoring `doc_id` would touch.

    `observations` is the count `_transition` will move; `keys` the aggregate keys it
    rebuilds (both labels, as `_transition` reads them). Runs on a read-only session.
    """
    from artmind import projection
    from artmind.graph_query import read_session

    label = "Observation" if to_history else "ObservationHistory"
    with read_session() as session:
        keys = projection.keys_for_document(session, doc_id)
        n = session.run(f"MATCH (o:{label} {{doc_id: $doc_id}}) RETURN count(o) AS n", doc_id=doc_id).single()
        doc = session.run(
            "MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND d.id = $doc_id RETURN d.name AS name LIMIT 1",
            doc_id=doc_id,
        ).single()
    return {
        "doc_id": doc_id,
        "name": doc.get("name") if doc else None,
        "observations": int(n["n"]) if n and n.get("n") is not None else 0,
        "keys": sorted(keys),
    }


def transition_effect(plan: dict, *, to_history: bool, domain: str | None) -> dict:
    """The `effect` block for a retire (`to_history`) or restore, from `plan_transition`."""
    from artmind import lifecycle_records
    from artmind.cli_conventions import Change, Effect
    from artmind.effects import topics_touching
    from artmind.observations import key_string

    doc_id, name = plan["doc_id"], plan["name"]
    label = name or doc_id
    record = f".artmind/data/curation/lifecycle/{lifecycle_records.record_id(doc_id)}.json"
    scope = f" --domain {domain}" if domain else ""
    verb, inverse = ("Retire", "restore") if to_history else ("Restore", "retire")
    where = "history" if to_history else "latest"
    return Effect(
        summary=(f"{verb} {label!r}: {plan['observations']} observation(s) move to {where}; "
                 f"{len(plan['keys'])} aggregate key(s) are rebuilt."),
        changes=[
            Change("document_retire" if to_history else "document_restore", 1, [{"doc_id": doc_id, "name": name}]),
            Change("observation_move", plan["observations"], []),
            Change("entity_change", len(plan["keys"]), [{"key": key_string(tuple(k))} for k in plan["keys"]]),
        ],
        stale_topics=topics_touching(
            entity_keys=[key_string(tuple(k)) for k in plan["keys"]], doc_ids=[doc_id], doc_names=[name] if name else []),
        reversible=True,
        undo=f"artmind docs {inverse}{scope} --documentName {doc_id}",
        travels=True, records=[record],
    ).to_json()


def retire_document(doc_id: str, domain: str | None = None, *, dry_run: bool = False) -> dict:
    """Move a document and everything it asserted from `latest` to `history`.

    The document and its observations stay in storage and stay reachable by
    asking for them; they leave every index. Entities left with no `latest`
    observation anywhere are deleted by the rebuild — not because this function
    decided they were orphans, but because nothing asserts them any more.

    The retirement travels (spec 2026-09-26 §15 A10): its record,
    `.artmind/data/curation/lifecycle/<sha1(doc_id)>.json`, is written first
    and the document is marked with it -- `vault sync` retires it on every
    other machine, and a later re-ingest or replay keeps it retired
    (`artmind.lifecycle_records`).

    `dry_run` reads what would move and writes nothing (no record, no label swap).
    """
    plan = plan_transition(doc_id, to_history=True)
    effect = transition_effect(plan, to_history=True, domain=domain)
    if dry_run:
        return {**plan, "projection": None, "effect": effect}
    with neo4j_session() as session:
        result = session.execute_write(_retire_tx, doc_id, domain)
    logger.info(
        "Retired {}: {} observation(s) → history, projection {}",
        doc_id, result["observations"], result["projection"],
    )
    if domain:
        _sweep(domain, result["keys"])
    return {**result, "effect": effect}


def restore_document(doc_id: str, domain: str | None = None, *, dry_run: bool = False) -> dict:
    """Move a document's assertions back from `history` to `latest`.

    The exact inverse. Because ids are deterministic and the projection is
    derived, restoring recreates the same entities with the same ids rather
    than a parallel set. Deletes the document's lifecycle record, so `vault
    sync` restores it on every other machine too. `dry_run` reads what would
    move and writes nothing (the record stays).
    """
    from artmind import curation_records, lifecycle_records

    plan = plan_transition(doc_id, to_history=False)
    effect = transition_effect(plan, to_history=False, domain=domain)
    if dry_run:
        return {**plan, "projection": None, "effect": effect}
    curation_records.delete_record(lifecycle_records.NAME, lifecycle_records.record_id(doc_id))
    with neo4j_session() as session:
        result = session.execute_write(_restore_tx, doc_id)
    logger.info(
        "Restored {}: {} observation(s) → latest, projection {}",
        doc_id, result["observations"], result["projection"],
    )
    if domain:
        _sweep(domain, result["keys"])
    return {**result, "effect": effect}


def _retire_tx(tx, doc_id: str, domain: str | None) -> dict:
    """`retire_document`'s transaction: the record file, then the label swap
    and rebuild, then the mark tying the document to its record."""
    from artmind import curation_records, lifecycle_records

    record = lifecycle_records.record_for(tx, doc_id, domain)
    fingerprint = curation_records.write_record(lifecycle_records.NAME, record)
    result = _transition(tx, doc_id, to_history=True)
    lifecycle_records.mark(tx, record, fingerprint)
    return result


def _restore_tx(tx, doc_id: str) -> dict:
    from artmind import lifecycle_records

    result = _transition(tx, doc_id, to_history=False)
    lifecycle_records.unmark(tx, doc_id)
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

    clause = " AND (d._domain = $domain OR d._domain STARTS WITH ($domain + '.'))" if domain else ""
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
