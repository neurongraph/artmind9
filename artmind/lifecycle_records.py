"""Lifecycle records -- the `lifecycle` curation kind (spec 2026-09-26 §7,
D6; §15 A10).

`docs retire` (and a document-scoped supersession, and archive restore) move
a document and everything it asserted from `latest` to `history`; `docs
restore` moves it back. That used to live only in the graph. A retired
document now also has a file, `.artmind/data/curation/lifecycle/<id>.json`:

    {"id": sha1(doc_id), "doc_id", "domain", "status": "retired"}

**The file exists exactly while the document is retired**: `docs retire`
writes it, `docs restore` deletes it. So `vault sync` retires a document when
its record appears and restores it when the record disappears.

**A retirement is sticky across re-ingest and replay.** Committing a
document (`ingest._commit_document_tx`) revives its node under `:Document`;
when a lifecycle record for its id exists, the same transaction moves it
straight back to history. Locally the commit reads the working tree's
record; `vault sync` also re-applies, after replaying a document, the record
committed at `head` (`Kind.reapply_for`). A retired document therefore stays
retired until `docs restore`, whatever order machines ingest, replay and
sync in.

The id is a hash because a document id may hold characters no file name can
(`table:<domain>:<table>`); the record carries the id itself.
"""
from __future__ import annotations

import hashlib

from loguru import logger

from artmind.curation_records import Kind, matched_count

NAME = "lifecycle"

_DOMAIN = (
    "MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND d.id = $doc_id "
    "RETURN d._domain AS domain LIMIT 1"
)
_MARK = (
    "MATCH (d:DocumentHistory {id: $doc_id}) "
    "SET d.lifecycle_record = $id, d.lifecycle_fingerprint = $fingerprint "
    "RETURN count(d) AS n"
)
_UNMARK = (
    "MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND d.id = $doc_id "
    "REMOVE d.lifecycle_record, d.lifecycle_fingerprint"
)
_HELD = (
    "MATCH (d:DocumentHistory {id: $doc_id}) WHERE d.lifecycle_record = $id "
    "RETURN count(d) AS n"
)
# A record whose content could not be read: the document is found by its mark.
_BY_RECORD = (
    "MATCH (d:DocumentHistory) WHERE d.lifecycle_record = $id "
    "RETURN d.id AS doc_id"
)
_READ_FINGERPRINTS = (
    "MATCH (d:DocumentHistory) WHERE d.lifecycle_record IN $ids "
    "RETURN d.lifecycle_record AS id, d.lifecycle_fingerprint AS fingerprint"
)


def record_id(doc_id: str) -> str:
    return hashlib.sha1(doc_id.encode("utf-8")).hexdigest()


def record_for(tx, doc_id: str, domain: str | None = None) -> dict:
    """The record retiring `doc_id`; its domain read from the graph when not
    given."""
    if domain is None:
        rec = tx.run(_DOMAIN, doc_id=doc_id).single()
        # `.single()` is a `neo4j.Record` (a tuple with a mapping interface),
        # not a dict: read it with `.get`, and check the value's type.
        value = rec.get("domain") if rec is not None else None
        domain = value if isinstance(value, str) else None
    return {"id": record_id(doc_id), "doc_id": doc_id, "domain": domain, "status": "retired"}


def retired_in_working_tree(doc_id: str | None) -> bool:
    """Does this working tree hold a lifecycle record retiring `doc_id`?"""
    from artmind import curation_records

    return bool(doc_id) and curation_records.read_record(NAME, record_id(doc_id)) is not None


def mark(tx, record: dict, fingerprint: str) -> int | None:
    """Record, on the retired document, which record retired it. Returns how
    many documents were marked (0: the document is not in this graph)."""
    return matched_count(tx.run(_MARK, doc_id=record["doc_id"], id=record["id"], fingerprint=fingerprint))


def unmark(tx, doc_id: str) -> None:
    tx.run(_UNMARK, doc_id=doc_id)


def apply(tx, record: dict, fingerprint: str) -> set:
    """Retire the document (idempotent: an already-retired one only gets its
    mark) and return the keys its observations feed, for the rebuild."""
    from artmind.lifecycle import _transition

    result = _transition(tx, record["doc_id"], to_history=True, rebuild=False)
    if mark(tx, record, fingerprint) == 0:
        # Not an error -- the document may not have reached this graph yet,
        # and a later sync (or its replay) applies the record again -- but
        # nothing was retired or stamped.
        logger.warning(
            "the lifecycle record {} was not applied: the document {} is not in this graph "
            "(neither live nor retired), so nothing was retired or marked",
            record["id"], record["doc_id"],
        )
    return {tuple(k) for k in result["keys"]}


def remove(tx, record: dict) -> set:
    """The record is gone: restore the document -- only when THIS record
    retired it. A document retracted since (its staging folder deleted),
    or retired before records existed, is left where it is. A stub record
    without a `doc_id` finds the document by its mark instead."""
    from artmind.lifecycle import _transition

    doc_id = record.get("doc_id")
    if doc_id:
        held = tx.run(_HELD, doc_id=doc_id, id=record["id"]).single()
        doc_ids = [doc_id] if held and held.get("n") else []
    else:
        # A stub record (its blob at base was unreadable): only the id is
        # known, so find the document by the mark it carries.
        rows = tx.run(_BY_RECORD, id=record["id"]).data()
        doc_ids = sorted({row["doc_id"] for row in rows if row.get("doc_id")})
    keys: set = set()
    for target in doc_ids:
        result = _transition(tx, target, to_history=False, rebuild=False)
        unmark(tx, target)
        keys |= {tuple(k) for k in result["keys"]}
    return keys


def read_fingerprints(ids: list[str], *, timeout: float | None = None) -> dict:
    from artmind import graph_query

    if not ids:
        return {}
    with graph_query.neo4j_session(timeout=timeout) as session:
        rows = session.run(_READ_FINGERPRINTS, ids=sorted(set(ids))).data()
    return {row["id"]: row["fingerprint"] for row in rows}


def domains(record: dict) -> list:
    return [record["domain"]] if record.get("domain") else []


def reapply_for(doc_ids: list[str]) -> list[str]:
    """The record ids to re-apply after `vault sync` replays `doc_ids`: a
    replay revives the node, and a record committed at `head` must win."""
    return sorted({record_id(d) for d in doc_ids if d})


KIND = Kind(
    name=NAME, phase="pre", apply=apply, remove=remove, read_fingerprints=read_fingerprints,
    domains=domains, reapply_for=reapply_for,
)
