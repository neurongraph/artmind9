"""Members of a set (spec 2026-11-10 §6.4): discover, choose, gather.

A set question ("which places have I visited in X") is answered by members the question does
not name. **Discover** (code, no LLM): resolve each anchor entity, then list the entities of
one class co-mentioned with any anchor (same source chunks), with their mention count, first
and last dated mention and domain. Code never decides membership: the build skill shows the
candidates, the user keeps, drops or adds members. **Gather**: each kept member is a subject
searched by its name and graph aliases, plus one cell for the anchors themselves.
"""
from __future__ import annotations

from artmind.graph_query import serialize_record

CANDIDATES_LIMIT = 300

_CANDIDATES = """
MATCH (t:Entity) WHERE t._id IN $anchorIds
MATCH (t)-[:AGGREGATES]->(:Observation)-[:EXTRACTED_FROM]->(c:DocChunk)
WITH collect(DISTINCT t) AS anchors, collect(DISTINCT c) AS chunks
UNWIND chunks AS c
MATCH (c)<-[:EXTRACTED_FROM]-(:Observation)<-[:AGGREGATES]-(e:Entity)
WHERE e.entity_class = $entityClass AND NOT e IN anchors AND e._domain IN $domains
OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
WITH e, count(DISTINCT c) AS mentions,
     min(coalesce(d._valid_from, d.valid_from)) AS first, max(coalesce(d._valid_from, d.valid_from)) AS last
RETURN e._id AS id, e.key AS key, e.name AS name, coalesce(e.aliases, []) AS aliases, e._domain AS domain,
       mentions, first, last
ORDER BY mentions DESC, name
LIMIT $limit
"""

_ALIASES = """
UNWIND $names AS n
OPTIONAL MATCH (e:Entity) WHERE e._domain IN $domains
  AND (toLower(e.name) = toLower(n) OR any(a IN coalesce(e.aliases, []) WHERE toLower(a) = toLower(n)))
WITH n, e ORDER BY coalesce(e._observation_count, 0) DESC
WITH n, collect(e)[0] AS e
RETURN n AS name, e.key AS key, coalesce(e.aliases, []) AS aliases
"""


def _fold(text) -> str:
    return " ".join(str(text or "").casefold().split())


def resolve_anchors(anchors: list[str], domains: list[str], *, resolve=None, entity_class: str | None = None
                    ) -> list[dict]:
    """[{name, _id, entity_class, resolved_name}] — an exact or alias match first (of `entity_class`
    when one is), else the top hit."""
    if resolve is None:
        from artmind.vector_query import entity_resolve as resolve
    out = []
    for name in anchors:
        rows = resolve(domains, name, 5).get("rows") or []
        exact = [r for r in rows if "exact" in (r.get("matched_by") or []) or "alias" in (r.get("matched_by") or [])]
        same = [r for r in exact if ((r.get("entity") or {}).get("entity_class")) == entity_class]
        hit = (same or exact or rows or [None])[0]
        entity = (hit or {}).get("entity") or {}
        out.append({"name": name, "_id": entity.get("_id"), "entity_class": entity.get("entity_class"),
                    "resolved_name": entity.get("name")})
    return out


def discover(anchors: list[str], entity_class: str, domains: list[str], session, *, kept=(), dropped=(),
             resolve=None) -> dict:
    """The dry run's `discover` block: resolved anchors and co-mentioned candidates of the class."""
    from artmind.vector_query import _expand

    scoped = _expand(list(domains))
    resolved = resolve_anchors(anchors, domains, resolve=resolve, entity_class=entity_class)
    ids = [a["_id"] for a in resolved if a["_id"]]
    rows = [serialize_record(r) for r in session.run(_CANDIDATES, anchorIds=ids, entityClass=entity_class,
                                                     domains=scoped, limit=CANDIDATES_LIMIT)] if ids else []
    kept_f = {_fold(k) for k in kept}
    dropped_f = {_fold(d) for d in dropped}
    candidates = []
    for r in rows:
        names = {_fold(r.get("name")), *(_fold(a) for a in r.get("aliases") or [])}
        candidates.append({"name": r["name"], "key": r.get("key"), "aliases": list(r.get("aliases") or []),
                           "domain": r.get("domain"), "mentions": int(r.get("mentions") or 0),
                           "first": r.get("first") and str(r["first"])[:10], "last": r.get("last") and str(r["last"])[:10],
                           "kept": bool(names & kept_f), "dropped": bool(names & dropped_f)})
    return {"anchors": resolved, "entity_class": entity_class, "candidates": candidates,
            "candidates_total": len(candidates), "capped": len(rows) >= CANDIDATES_LIMIT}


def new_candidates(block: dict) -> list[str]:
    """Candidates neither kept nor dropped (freshness reports them, never adds them)."""
    return [c["name"] for c in block.get("candidates") or [] if not c["kept"] and not c["dropped"]]


def member_records(names: list[str], domains: list[str], session) -> list[dict]:
    """[{name, entity_key?, aliases}] — a member need not be an entity (then no key, no aliases)."""
    from artmind.vector_query import _expand

    if not names:
        return []
    found = {r["name"]: r for r in (serialize_record(x) for x in session.run(
        _ALIASES, names=list(names), domains=_expand(list(domains))))}
    out = []
    for n in names:
        r = found.get(n) or {}
        rec = {"name": n, "aliases": sorted({a for a in r.get("aliases") or [] if _fold(a) != _fold(n)})}
        if r.get("key"):
            rec["entity_key"] = r["key"]
        out.append(rec)
    return out


def query_text(member: dict) -> str:
    """What a member's cell searches for: its name and its aliases."""
    return " ".join([member["name"], *(member.get("aliases") or [])])


def anchors_subject(anchors) -> str:
    return "; ".join(anchors)
