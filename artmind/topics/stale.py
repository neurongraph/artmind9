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


MIN_OVERLAP = 2

# The index's one read for `query survey`: how many pages are indexed, and the pages citing at
# least MIN_OVERLAP of the returned documents with their domains. The page files stay the source
# of the displayed fields; the node only says which pages match.
_CITING = f"""
MATCH (t:TopicPage)
WITH count(t) AS indexed
CALL (indexed) {{
  OPTIONAL MATCH (p:TopicPage)-[:CITES]->(d:Document) WHERE d.id IN $docIds
  WITH p, count(DISTINCT d) AS overlap
  WHERE p IS NOT NULL AND overlap >= {MIN_OVERLAP}
  RETURN collect({{slug: p.slug, domains: p.domains, overlap: overlap}}) AS hits
}}
RETURN indexed, hits
"""

_INDEX_EMPTY = ("topic index is empty, so topic pages were found by scanning the files; "
                "run `artmind topic index`")


class Found(list):
    """The matched pages, plus the `warnings` the survey adds to its own."""

    def __init__(self, items=(), warnings=()):
        super().__init__(items)
        self.warnings = list(warnings)


def _summaries(matches: list[tuple[store.Page, int]]) -> list[dict]:
    if not matches:
        return []
    with read_session() as session:
        reasons = staleness([p for p, _ in matches], session)
    return [{"slug": p.slug, "topic": p.meta.get("topic"), "built": p.meta.get("built"),
             "stale": bool(reasons.get(p.slug)), "overlap": n} for p, n in matches]


def _indexed_matches(directory, doc_ids: list[str], domains: list[str]):
    """(matches, indexed): pages the index says cite the documents, read from their files; None
    when the index cannot answer."""
    try:
        with read_session() as session:
            row = (session.run(_CITING, docIds=sorted(set(doc_ids))).data() or [{}])[0]
    except Exception:
        return None, 0
    indexed = int(row.get("indexed") or 0)
    found = []
    for hit in row.get("hits") or []:
        if not any(store._related(str(a), b) for a in hit.get("domains") or [] for b in domains):
            continue
        page = store.read_page(directory, hit["slug"])
        if page is not None:  # a node whose file is gone is the sweep's to delete
            found.append((page, int(hit["overlap"])))
    return sorted(found, key=lambda t: (-t[1], t[0].slug)), indexed


def survey_topics(doc_ids: list[str], domains: list[str]) -> Found:
    """`query survey`'s `topics`: pages citing at least two of its documents.

    Asked of the topic index in one query (spec 2026-11-02 §6). An empty or unreachable index
    falls back to scanning the page files, with a warning naming `artmind topic index`."""
    import paths

    directory = getattr(paths, "TOPICS_DIR", None)
    if directory is None:
        return Found()
    matches, indexed = _indexed_matches(directory, doc_ids, domains)
    if matches is not None and indexed:
        return Found(_summaries(matches))
    pages = store.list_pages(directory)
    warnings = [_INDEX_EMPTY if matches is not None else
                "topic index unavailable, so topic pages were found by scanning the files; "
                "run `artmind topic index`"] if pages else []
    return Found(_summaries(store.matching(pages, doc_ids, domains, MIN_OVERLAP)), warnings)
