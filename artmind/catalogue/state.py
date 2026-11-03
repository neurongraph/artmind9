"""`:DomainState` -- a per-domain generation, bumped with the change it counts.

Spec `docs/superpowers/specs/2026-11-02-catalogue-and-answer-assets-design.md` §3.
Every writer that changes what a catalogue reads calls `bump` (or
`bump_for_document`) **inside its own transaction**, so the counter and the
change commit together or not at all. The write paths that must call it are
listed in `test/test_catalogue_bump_sites.py`, which fails when one stops.

Callers pass the leaf domains they wrote. A parent domain is never bumped
itself: `generations` sums a parent's children when it reads.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

CONSTRAINT_CYPHER = (
    "CREATE CONSTRAINT domain_state_domain IF NOT EXISTS "
    "FOR (n:DomainState) REQUIRE n._domain IS UNIQUE"
)

_SET = "SET s.generation = coalesce(s.generation, 0) + 1, s.changed_at = $now"

BUMP_CYPHER = f"""
UNWIND $domains AS d
MERGE (s:DomainState {{_domain: d}})
{_SET}
"""

# The document's own domain, read in the same transaction: for a caller that
# holds a document id and not its domain (retire / restore).
BUMP_FOR_DOCUMENT_CYPHER = f"""
MATCH (d) WHERE (d:Document OR d:DocumentHistory) AND d.id = $doc_id
WITH DISTINCT d._domain AS dom WHERE dom IS NOT NULL AND dom <> ''
MERGE (s:DomainState {{_domain: dom}})
{_SET}
"""

_READ_CYPHER = """
MATCH (s:DomainState)
WHERE {predicate}
RETURN s._domain AS domain, s.generation AS generation
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalise(domains: Iterable[str | None]) -> list[str]:
    """Sorted, de-duplicated, blanks dropped."""
    return sorted({d.strip() for d in domains if d and d.strip()})


def bump(tx, domains: Iterable[str | None]) -> list[str]:
    """Increment the generation of each domain, in the caller's transaction.

    One statement for all of them; nothing is sent for an empty list. Returns
    the domains bumped.
    """
    names = normalise(domains)
    if names:
        tx.run(BUMP_CYPHER, domains=names, now=_now())
    return names


def bump_for_document(tx, doc_id: str) -> None:
    """Increment the generation of the domain `doc_id` belongs to (either
    label), in the caller's transaction."""
    tx.run(BUMP_FOR_DOCUMENT_CYPHER, doc_id=doc_id, now=_now())


def generations(session, domains: Iterable[str]) -> dict[str, int]:
    """The generation of each requested domain: its own node plus every child's
    (`a` covers `a.b`), 0 for a domain nothing has written. One indexed read."""
    from artmind.graph_query import domain_predicate

    wanted = normalise(domains)
    if not wanted:
        return {}
    rows = session.run(_READ_CYPHER.format(predicate=domain_predicate("s")), domains=wanted).data()
    out = {d: 0 for d in wanted}
    for row in rows:
        name, value = row["domain"], int(row.get("generation") or 0)
        for d in wanted:
            if name == d or name.startswith(d + "."):
                out[d] += value
    return out
