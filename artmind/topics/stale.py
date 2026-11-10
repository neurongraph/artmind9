"""When a topic page is stale (spec 2026-11-01 §3.4): a cited document's version
(content sha, else staging fingerprint) differs from the one recorded, a cited
document is no longer a live `:Document`, or an entity anchor's
`_observation_set_hash` changed. One query per kind for any number of pages."""
from __future__ import annotations

from artmind.graph_query import read_session
from artmind.topics import store

_SOURCES = """
UNWIND $docIds AS did
OPTIONAL MATCH (d:Document {id: did})
RETURN did AS doc_id, d IS NOT NULL AS live, coalesce(d.content_sha256, d.fingerprint) AS version
"""

_ENTITIES = """
UNWIND $keys AS k
OPTIONAL MATCH (e:Entity {key: k})
RETURN k AS key, e._observation_set_hash AS hash
"""


def _sources(page: store.Page) -> list[dict]:
    return [s for s in page.meta.get("sources") or [] if isinstance(s, dict)]


def staleness(pages: list[store.Page], session) -> dict[str, list[str]]:
    if not pages:
        return {}
    doc_ids = sorted({s["doc_id"] for p in pages for s in _sources(p) if s.get("doc_id")})
    keys = sorted({(p.meta.get("anchor") or {}).get("key") for p in pages
                   if (p.meta.get("anchor") or {}).get("kind") == "entity" and (p.meta.get("anchor") or {}).get("key")})
    docs = {r["doc_id"]: dict(r) for r in session.run(_SOURCES, docIds=doc_ids)} if doc_ids else {}
    hashes = {r["key"]: r["hash"] for r in session.run(_ENTITIES, keys=keys)} if keys else {}
    out: dict[str, list[str]] = {}
    for page in pages:
        reasons = []
        for s in _sources(page):
            row = docs.get(s.get("doc_id"))
            if row is None or not row.get("live"):
                reasons.append(f"source retired: {s.get('name')}")
            elif row.get("version") != s.get("content_sha"):
                reasons.append(f"source changed: {s.get('name')}")
        anchor = page.meta.get("anchor") or {}
        if anchor.get("kind") == "entity" and hashes.get(anchor.get("key")) != anchor.get("observation_set_hash"):
            reasons.append(f"entity changed: {anchor.get('name') or anchor.get('key')}")
        out[page.slug] = reasons
    return out


def page_summary(page: store.Page, reasons: list[str]) -> dict:
    return {
        "slug": page.slug, "topic": page.meta.get("topic"), "domains": page.meta.get("domains") or [],
        "built": page.meta.get("built"), "anchor": (page.meta.get("anchor") or {}).get("kind"),
        "sources": len(_sources(page)), "stale": bool(reasons), "stale_reasons": reasons,
        "checks": page.meta.get("checks") or {},
    }
