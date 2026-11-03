"""The `health` catalogue part: data problems the operator should look at (spec 2026-11-02 §4).

Counts plus up to `SAMPLES_MAX` samples per field. It runs many queries and a staging
scan, so it is never built on a plan's path: `cache.get` returns what is cached (or
nothing), `refresh` rebuilds it on a background thread in the long-lived servers, and
`artmind catalogue refresh` builds it on request. A field whose reader fails is
`{"error": "..."}`; one broken source never hides the others. Samples hold entity,
document and proposal names: they live in the private `logs/` cache and in command
output, never in logs or the checkout.
"""
from __future__ import annotations

import os

from loguru import logger

from artmind import graph_query
from artmind.graph_query import domain_predicate

SAMPLES_MAX = 10
HEALTH_TTL_DEFAULT_S = 3600.0

FIELDS = ("twins", "same_as_open", "conflicts_open", "summaries_missing", "unembedded",
          "empty_extractions", "behind_vault", "drift")

_TWINS_CYPHER = f"""
MATCH (e:Entity)
WHERE {domain_predicate("e")} AND e.name IS NOT NULL
WITH e._domain AS domain, e.entity_class AS entity_class, toLower(e.name) AS name, count(e) AS entities
WHERE entities > 1
RETURN domain, entity_class, name, entities
ORDER BY entities DESC, name, entity_class, domain
"""

_SAME_AS_CYPHER = """
MATCH (p:SameAsProposal)
WHERE p.status = 'open'
RETURN p.id AS id, p.canonical AS canonical, p.members AS members
ORDER BY p.id
"""

_CONFLICTS_CYPHER = f"""
MATCH (co:Conflict)
WHERE {domain_predicate("co")} AND coalesce(co.status, 'open') = 'open'
RETURN co.id AS id, co._domain AS domain
ORDER BY co.id
"""

_SUMMARIES_CYPHER = f"""
MATCH (d:Document)
WHERE {domain_predicate("d")}
  AND NOT EXISTS {{ MATCH (:DocSummary {{level: 'document'}})-[:SUMMARIZES]->(d) }}
RETURN d.id AS id, d.name AS name, d._domain AS domain
ORDER BY d.name, d.id
"""


def health_ttl_s() -> float:
    """`ARTMIND_CATALOGUE_HEALTH_TTL_S`: how long a built `health` part is fresh (default 3600)."""
    raw = (os.environ.get("ARTMIND_CATALOGUE_HEALTH_TTL_S") or "").strip()
    if not raw:
        return HEALTH_TTL_DEFAULT_S
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if value <= 0:
        logger.warning("catalogue: ARTMIND_CATALOGUE_HEALTH_TTL_S={!r} is not a positive number; using {}", raw, HEALTH_TTL_DEFAULT_S)
        return HEALTH_TTL_DEFAULT_S
    return value


def _field(rows: list) -> dict:
    return {"count": len(rows), "samples": rows[:SAMPLES_MAX]}


def _in_family(domain: str, wanted: str) -> bool:
    return domain == wanted or domain.startswith(wanted + ".")


def _twins(domain: str) -> dict:
    rows = graph_query._run_read_query(_TWINS_CYPHER, {"domains": [domain]})
    return _field([{"domain": r.get("domain"), "entity_class": r.get("entity_class"),
                    "name": r.get("name"), "entities": int(r.get("entities") or 0)} for r in rows])


def _same_as_open(domain: str) -> dict:
    rows = graph_query._run_read_query(_SAME_AS_CYPHER, {})
    mine = []
    for r in rows:
        keys = [str(m).split("|") for m in (r.get("members") or ())]
        domains = sorted({k[2] for k in keys if len(k) == 3 and _in_family(k[2], domain)})
        if domains:
            mine.append({"id": r.get("id"), "canonical": r.get("canonical"),
                         "members": len(keys), "domain": domains[0]})
    return _field(mine)


def _conflicts_open(domain: str) -> dict:
    rows = graph_query._run_read_query(_CONFLICTS_CYPHER, {"domains": [domain]})
    return _field([{"id": r.get("id"), "domain": r.get("domain")} for r in rows])


def _summaries_missing(domain: str) -> dict:
    rows = graph_query._run_read_query(_SUMMARIES_CYPHER, {"domains": [domain]})
    return _field([{"id": r.get("id"), "name": r.get("name"), "domain": r.get("domain")} for r in rows])


def _gaps() -> dict:
    from artmind import projection

    with graph_query.read_session() as session:
        return projection._gaps_by_domain(session)


def _unembedded(domain: str) -> dict:
    """The gaps `projection status` counts: entities, observation keys without an entity,
    passages. (Summaries are embedded by the same passage sweep and not counted separately.)"""
    totals = {"unembedded_entities": 0, "unprojected_keys": 0, "unembedded_passages": 0}
    by_domain = []
    for dom, gap in _gaps().items():
        if not _in_family(dom, domain):
            continue
        for k in totals:
            totals[k] += int(gap.get(k) or 0)
        if any(gap.values()):
            by_domain.append({"domain": dom, **{k: int(gap.get(k) or 0) for k in totals}})
    return {"count": sum(totals.values()), **totals, "samples": by_domain[:SAMPLES_MAX]}


def _empty_extractions(domain: str) -> dict:
    import paths
    from artmind import staging_audit

    notes = staging_audit.empty_extractions(paths.KG_DIR, domains=[domain])["notes"]
    return _field([{"path": n.get("path"), "domain": n.get("domain"), "chars": n.get("chars")} for n in notes])


def _behind_vault(domain: str) -> dict:
    """What the graph and the structured store still have to apply from the vault. The
    document count is vault-wide (git cannot attribute it to a domain); tables are this
    domain's."""
    import paths
    from artmind import vault_sync

    vault = paths.ARTMIND_VAULT_DIR
    if vault is None:
        return {"count": 0, "docs": 0, "tables": [], "scope": "no vault", "samples": []}
    stores = vault_sync.status_report(vault).get("stores") or {}
    docs = sum(int((s or {}).get("docs") or 0) for s in stores.values())
    tables = sorted({f"{d}.{t}" for s in stores.values() for d, t in ((s or {}).get("tables") or ())
                     if _in_family(d, domain)})
    return {"count": docs + len(tables), "docs": docs, "tables": tables[:SAMPLES_MAX],
            "scope": "documents are vault-wide", "samples": []}


def _drift(domain: str) -> dict:
    from artmind import projection

    with graph_query.read_session() as session:
        status = projection.status(session)
    flags = {k: bool(status.get(k)) for k in ("drift", "same_as_drift", "schema_drift")}
    return {"count": int(flags["drift"]), **flags, "samples": []}


_READERS = {
    "twins": _twins, "same_as_open": _same_as_open, "conflicts_open": _conflicts_open,
    "summaries_missing": _summaries_missing, "unembedded": _unembedded,
    "empty_extractions": _empty_extractions, "behind_vault": _behind_vault, "drift": _drift,
}


def build_health(domain: str) -> dict:
    out = {}
    for name in FIELDS:
        try:
            out[name] = _READERS[name](domain)
        except Exception as exc:  # one broken source must not hide the others
            logger.warning("catalogue health: {} failed for {}: {}", name, domain, type(exc).__name__)
            out[name] = {"error": f"{type(exc).__name__}: {exc}"}
    return {"health": out}
