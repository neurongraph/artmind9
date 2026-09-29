"""Synthesis records -- the `syntheses` curation kind (spec 2026-09-26 §7,
D6; §15 A10).

`projection synthesize` asks an LLM to rewrite an entity's description from
all its observations and stores the result as a `:Synthesis {id: entity id}`
node, which every projection rebuild reads back (`synthesis_loader`). It
lived only in the graph, so a machine with its own Neo4j paid for it again --
or never had it. Each synthesis is now also a file,
`.artmind/data/curation/syntheses/<entity id>.json`: the node's properties
plus the entity's aggregate `key`.

No embedding is stored: the entity's vector is derived (description + local
model). A rebuild that applies the synthesis flags the entity
`embedding_stale`, and `vault sync`'s entity sweep re-embeds it locally --
the same reason chunk vectors live in a gitignored sidecar.

Applied before `vault sync`'s union rebuild ("pre"), with the entity's key
joining it, so the rebuilt description is the synthesis. Removing the file
removes the node and rebuilds the key, falling back to the winning
observation's description.

**Applying needs nothing else to exist first.** The record IS the
`:Synthesis` node, written in one statement together with its fingerprint --
so the fingerprint can never be on the graph without the content it vouches
for. The entity need not exist yet (the rebuild the returned key joins
creates it), and a node whose observations have not arrived on this machine
is still the right thing to hold: the rebuild that later projects them reads
it back.
"""
from __future__ import annotations

from artmind.curation_records import Kind

NAME = "syntheses"

_APPLY = "MERGE (s:Synthesis {id: $id}) SET s = $props"
_REMOVE = "MATCH (s:Synthesis {id: $id}) DETACH DELETE s"
# A record whose content could not be read: the entity's key is read off the
# node, which carries it (`record_from` puts it in the properties).
_KEY_OF = "MATCH (s:Synthesis {id: $id}) RETURN s.key AS key"
_READ_FINGERPRINTS = (
    "MATCH (s:Synthesis) WHERE s.id IN $ids "
    "RETURN s.id AS id, s.record_fingerprint AS fingerprint"
)


def record_from(key: tuple[str, str, str], synthesis: dict) -> dict:
    """The record for one synthesis: its node properties and the key."""
    from artmind.observations import key_string

    return {**synthesis, "key": key_string(key)}


def _key_from(text) -> set:
    parts = str(text or "").split("|")
    return {tuple(parts)} if len(parts) == 3 else set()


def _key(record: dict) -> set:
    return _key_from(record.get("key"))


def apply(tx, record: dict, fingerprint: str) -> set:
    """Write the `:Synthesis` node from the record; its key must be rebuilt
    for the description to follow."""
    tx.run(_APPLY, id=record["id"], props={**record, "record_fingerprint": fingerprint})
    return _key(record)


def remove(tx, record: dict) -> set:
    """Delete the node and hand back its key, so the entity is rebuilt
    without it. A stub record (`{"id": ...}`, its blob at base unreadable)
    has no key: it is read off the node before the node goes."""
    keys = _key(record)
    if not keys:
        # `.single()` is a `neo4j.Record`, not a dict: read it with `.get`.
        found = tx.run(_KEY_OF, id=record["id"]).single()
        keys = _key_from(found.get("key") if found is not None else None)
    tx.run(_REMOVE, id=record["id"])
    return keys


def read_fingerprints(ids: list[str], *, timeout: float | None = None) -> dict:
    from artmind import graph_query

    if not ids:
        return {}
    with graph_query.neo4j_session(timeout=timeout) as session:
        rows = session.run(_READ_FINGERPRINTS, ids=sorted(set(ids))).data()
    return {row["id"]: row["fingerprint"] for row in rows}


def domains(record: dict) -> list:
    return [key[2] for key in _key(record)]


KIND = Kind(name=NAME, phase="pre", apply=apply, remove=remove, read_fingerprints=read_fingerprints, domains=domains)
