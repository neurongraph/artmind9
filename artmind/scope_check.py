"""Out-of-scope check for `vector-text` and `survey`: a fact about the result.

When a search names domains narrower than their domain root, the same question's
vector leg also runs over the rest of that root (the query embedding is reused).
Documents there whose best passage is closer to the question than the best row the
search returned are reported as `outside_scope` (with that best passage, so the
evidence is in the result), with a warning; the CLI adds a
`next` that reruns the search on the domain root. Nothing is reported when the
search already covers its roots, when the embedding is unavailable, or when nothing
outside beats what was returned. No routing: the agent decides whether to widen.
"""
from __future__ import annotations

from artmind import vector_query

OUTSIDE_POOL = 50
OUTSIDE_LIMIT = 3
PASSAGE_CHARS = 600

_DOCUMENTS = """
UNWIND $docIds AS id
MATCH (d:Document {id: id})
RETURN d.id AS doc_id, coalesce(d.title, d.name) AS title, d._domain AS domain
"""


def roots(domains) -> list[str]:
    """Each domain's root (the part before its first `.`), in first-seen order."""
    out: list[str] = []
    for d in domains:
        root = str(d).split(".", 1)[0]
        if root not in out:
            out.append(root)
    return out


def outside_domains(domains) -> list[str]:
    """Domains under the searched domains' roots that the search did not cover."""
    inside = set(vector_query._expand(list(domains)))
    family = vector_query._expand(roots(domains))
    return [d for d in family if d not in inside]


def outside_scope(session, domains, embedding, best_relevance, as_of=None,
                  limit: int = OUTSIDE_LIMIT) -> list[dict]:
    """Up to `limit` documents outside the searched domains whose best passage cosine
    beats `best_relevance` (any match counts when the search returned no vector match)."""
    if embedding is None:
        return []
    outside = outside_domains(domains)
    if not outside:
        return []
    params = {"domains": outside, "pool": OUTSIDE_POOL, "embedding": embedding,
              **({"asOf": as_of} if as_of else {})}
    best: dict[str, dict] = {}
    for row in vector_query._rows(session, vector_query._passage_vector_cypher(as_of), params):
        doc_id, cosine = row.get("doc_id"), row.get("cosine")
        if doc_id and cosine is not None and cosine > (best.get(doc_id) or {}).get("cosine", float("-inf")):
            best[doc_id] = row
    stronger = sorted((r for r in best.values() if best_relevance is None or r["cosine"] > best_relevance),
                      key=lambda r: -r["cosine"])[:limit]
    if not stronger:
        return []
    meta = {r["doc_id"]: r for r in (dict(x) for x in session.run(_DOCUMENTS, docIds=[r["doc_id"] for r in stronger]))}
    return [{"doc_id": r["doc_id"], "title": (meta.get(r["doc_id"]) or {}).get("title"),
             "domain": (meta.get(r["doc_id"]) or {}).get("domain"), "relevance": round(r["cosine"], 4),
             "passage_id": r.get("id"), "chunk_id": r.get("chunk_id"), "passage": _clip(r.get("text"))}
            for r in stronger]


def _clip(text, limit: int = PASSAGE_CHARS):
    """The passage text, cut at a word boundary to `limit` characters (marked with an ellipsis)."""
    if not text or len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut + " …"


def warning(found: list[dict], best_relevance) -> str:
    """One warning line naming what outside the searched domains matched better."""
    names = "; ".join(f"{f.get('title') or f['doc_id']} ({f.get('domain')}, {f['relevance']})" for f in found)
    than = f" than the best row returned ({round(best_relevance, 4)})" if best_relevance is not None else ""
    return f"outside the searched domains, {len(found)} document(s) match the question more closely{than}: {names}"
