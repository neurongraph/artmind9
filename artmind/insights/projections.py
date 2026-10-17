"""Named read-only Cypher projections for the insights pipeline (spec 2026-10-17 §5).

Every statement is `$domains`-scoped, runs in a server-enforced read-only session
under a 60 s timeout, and is memoised per run, so detectors sharing a projection
pay for it once. Detectors never hold a driver themselves.
"""

from __future__ import annotations

import json

from neo4j import Query
from neo4j.exceptions import DriverError, Neo4jError

from artmind.graph_query import read_session
from artmind.insights.model import InsightsError

QUERY_TIMEOUT_S = 60
# Free text or identity by construction: never a vocabulary worth profiling.
SKIP_PROPERTY_KEYS = ("embedding", "embedding_stale", "name", "aliases", "description", "context", "key", "entity_class")

PROJECTIONS: dict[str, str] = {
    # One row per entity: the distinct document valid dates it was observed on.
    "entity_evidence": """
MATCH (e:Entity)-[:AGGREGATES]->(o:Observation)
WHERE e._domain IN $domains
WITH e, collect(DISTINCT o._doc_valid_from) AS days, count(DISTINCT o.doc_id) AS docs
RETURN e._id AS _id, e.name AS name, e.entity_class AS cls, e.type AS type, days AS days, docs AS docs
ORDER BY _id
""",
    "documents": """
MATCH (d:Document)
WHERE d._domain IN $domains
RETURN d.id AS id, d._valid_from AS valid_from, d._valid_time_source AS time_source
ORDER BY id
""",
    "property_stats": """
MATCH (e:Entity)
WHERE e._domain IN $domains
UNWIND [k IN keys(e) WHERE NOT k STARTS WITH '_' AND NOT k IN $skip_keys] AS k
WITH e.entity_class AS cls, k, e[k] AS v
WITH cls, k, CASE WHEN valueType(v) STARTS WITH 'LIST' THEN null ELSE toString(v) END AS sv
ORDER BY sv
WITH cls, k, count(*) AS filled, count(DISTINCT sv) AS distinct_values, collect(DISTINCT sv) AS vals
RETURN cls AS cls, k AS property, filled AS filled, distinct_values AS distinct_values, vals[..200] AS sample
ORDER BY cls, property
""",
    "relation_triples": """
MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity)
WHERE a._domain IN $domains AND b._domain IN $domains
WITH a, r, b ORDER BY b._id
WITH r.rel_type AS rel, a.entity_class AS src, b.entity_class AS dst, count(*) AS n, collect(DISTINCT b._id) AS targets
RETURN rel AS rel, src AS src, dst AS dst, n AS n, targets[..$max_targets] AS targets
ORDER BY n DESC, rel, src, dst
""",
    "open_conflicts": """
MATCH (c:Conflict)-[:CONFLICT_OF]->(e:Entity)
WHERE e._domain IN $domains AND c._domain IN $domains AND c.status = 'open'
RETURN e.entity_class AS cls, c.property AS property, count(DISTINCT c) AS n
ORDER BY n DESC, cls, property
""",
}


class Projector:
    def __init__(self, domains: list[str], session_factory=None):
        if not domains:
            raise InsightsError("no domains to project")
        self.domains = list(domains)
        self._session_factory = session_factory or read_session
        self._memo: dict[tuple[str, str], list[dict]] = {}

    def rows(self, name: str, **params) -> list[dict]:
        if name not in PROJECTIONS:
            raise InsightsError(f"unknown projection {name!r}")
        memo_key = (name, json.dumps(params, sort_keys=True, default=str))
        if memo_key in self._memo:
            return self._memo[memo_key]
        bindings = {**params, "domains": self.domains}
        try:
            with self._session_factory() as session:
                rows = [dict(rec) for rec in session.run(Query(PROJECTIONS[name], timeout=QUERY_TIMEOUT_S), bindings)]
        except (Neo4jError, DriverError) as exc:
            detail = getattr(exc, "message", None) or str(exc) or exc.__class__.__name__
            raise InsightsError(f"projection {name} failed: {detail}") from exc
        self._memo[memo_key] = rows
        return rows
