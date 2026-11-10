"""The granularity ladder's level 2 (spec 2026-11-10 §2, §9.2–§9.3): per domain, **n** is the
90th percentile of `_observation_count` (at least MIN_N); an entity at or above n is an
anchor. `topic build --dryRun` lists anchors whose names or aliases carry a topic word as
suggested subjects; the caller's `--facet` list is still what is built."""
from __future__ import annotations

from artmind.graph_query import sanitize_lucene_query, serialize_record

MIN_N = 5
SUGGEST_LIMIT = 10
MIN_TERM = 4

_THRESHOLDS = """
MATCH (e:Entity) WHERE e._domain IN $domains AND e._observation_count IS NOT NULL
RETURN e._domain AS domain, percentileDisc(e._observation_count, 0.9) AS p90,
       count(e) AS entities
"""

_ANCHORS = """
MATCH (e:Entity) WHERE e._domain IN $domains AND e._observation_count >= $min_n
  AND any(t IN $terms WHERE toLower(e.name) CONTAINS t
          OR any(a IN coalesce(e.aliases, []) WHERE toLower(a) CONTAINS t))
RETURN e._id AS id, e.name AS name, e.entity_class AS entity_class, e._domain AS domain,
       e._observation_count AS observation_count
ORDER BY observation_count DESC, name
LIMIT $limit
"""


def thresholds(session, domains: list[str]) -> dict[str, dict]:
    """{domain: {n, entities}} for every expanded domain holding entities."""
    out = {}
    for r in (serialize_record(x) for x in session.run(_THRESHOLDS, domains=list(domains))):
        out[r["domain"]] = {"n": max(MIN_N, int(r.get("p90") or 0)), "entities": int(r.get("entities") or 0)}
    return out


def terms(topic: str) -> list[str]:
    words = sanitize_lucene_query(topic, drop_stopwords=True).replace('"', " ").split()
    return sorted({w.casefold() for w in words if len(w) >= MIN_TERM and w.isalnum()})


def suggested_subjects(topic: str, domains: list[str], session) -> list[dict]:
    from artmind.vector_query import _expand

    scoped = _expand(list(domains))
    words = terms(topic)
    if not words:
        return []
    ns = thresholds(session, scoped)
    if not ns:
        return []
    rows = [serialize_record(x) for x in session.run(_ANCHORS, domains=scoped, terms=words,
                                                      min_n=min(v["n"] for v in ns.values()),
                                                      limit=SUGGEST_LIMIT * 3)]
    out = []
    for r in rows:
        n = (ns.get(r["domain"]) or {}).get("n", MIN_N)
        if int(r.get("observation_count") or 0) >= n:
            out.append({"name": r["name"], "entity_class": r.get("entity_class"), "domain": r["domain"],
                        "observation_count": int(r["observation_count"]), "n": n, "_id": r["id"]})
    return out[:SUGGEST_LIMIT]
