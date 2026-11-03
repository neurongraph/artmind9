"""`artmind query survey` (spec 2026-11-01 §2): the documents a question needs
when its answer spans documents. Three legs each rank documents and RRF fuses
them (`retrieval_rank.survey_fuse`):

- D: document summaries (`:DocSummary` level `document`), vector + keyword 2:1
- C: chunk summaries (level `chunk`), best hit per document
- P: passages, Spec A's fused ranking, best chunk per document

A document without summaries is ranked by P alone; `warnings` says how many
documents in scope lack one. `vector-text` is unchanged: it is the needle tool.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from neo4j.exceptions import ClientError

from artmind import retrieval_rank, vector_query
from artmind.graph_query import read_session, sanitize_lucene_query, serialize_record

LEGS = ("D", "C", "P")
POOL = 300
MAX_TOP_DOCS = 50
#: The CLI allows 1-3 passages per document; `topic build` asks for more.
MAX_PASSAGES = 8
MISSING_SUMMARY_INDEX = (
    "the summary index is missing: run `artmind setup` inside the vault, then "
    "`artmind ingest summarise` to build summaries."
)

#: `topics(doc_ids, domains)`: pages that cite the returned documents (slice 3).
Topics = Callable[[list[str], list[str]], list[dict]]

_SUMMARY_ROW = "kind: 'summary', id: s.id, doc_id: s.doc_id, chunk_id: s.chunk_id, text: s.text"

_SUMMARY_VECTOR = f"""
CYPHER 25
MATCH (s:DocSummary)
  SEARCH s IN (
    VECTOR INDEX summary_embedding
    FOR $embedding
    WHERE s._domain IN $domains AND s.level = $level
    LIMIT $pool
  )
WITH s, vector.similarity.cosine(s.embedding, $embedding) AS score
RETURN s {{ {_SUMMARY_ROW}, cosine: score }} AS row
ORDER BY score DESC
"""

_SUMMARY_TEXT = f"""
CALL db.index.fulltext.queryNodes('summary_text_ft', $ft_query)
YIELD node AS s, score
WHERE s._domain IN $domains AND s.level = $level
RETURN s {{ {_SUMMARY_ROW} }} AS row
ORDER BY score DESC
LIMIT $pool
"""

_DOCUMENTS = """
UNWIND $docIds AS did
MATCH (d:Document {id: did})
OPTIONAL MATCH (s:DocSummary {id: did + ':summary'})
RETURN d.id AS doc_id, coalesce(d.title, d.name) AS title, d.name AS name, d._domain AS domain,
       s.text AS summary
"""

_COVERAGE = """
MATCH (d:Document) WHERE d._domain IN $domains
RETURN d._domain AS domain, count(d) AS total,
       count(CASE WHEN EXISTS { MATCH (:DocSummary {level: 'document'})-[:SUMMARIZES]->(d) } THEN 1 END) AS summarised
ORDER BY domain
"""


def _is_missing_summary_index(e: ClientError) -> bool:
    text = str(e).lower()
    return "summary_embedding" in text or "summary_text_ft" in text


def _summary_leg(session, level: str, embedding, ft_query: str, params: dict) -> list[dict]:
    vec = (vector_query._rows(session, _SUMMARY_VECTOR, {**params, "level": level, "embedding": embedding})
           if embedding is not None else [])
    text = (vector_query._rows(session, _SUMMARY_TEXT, {**params, "level": level, "ft_query": ft_query})
            if ft_query else [])
    return retrieval_rank.fuse_ids(vec, text)


def survey_search(
    domains,
    question: str,
    top_docs: int = 10,
    passages: int = 1,
    *,
    legs: Sequence[str] = LEGS,
    topics: Topics | None = None,
) -> dict:
    from artmind.graph_query import _domain_output, normalize_domains

    top_docs, passages = int(top_docs), int(passages)
    if not 1 <= top_docs <= MAX_TOP_DOCS:
        raise ValueError(f"--topDocs must be between 1 and {MAX_TOP_DOCS}")
    if not 1 <= passages <= MAX_PASSAGES:
        raise ValueError(f"--passages must be between 1 and {MAX_PASSAGES}")
    legs = tuple(leg for leg in LEGS if leg in set(legs))
    if not legs:
        raise ValueError("--legs needs at least one of D, C, P")
    domains = normalize_domains(domains)
    expanded = vector_query._expand(domains)
    warnings: list[str] = []
    ft_query = sanitize_lucene_query(question, drop_stopwords=True)
    embedding = None
    try:
        embedding = vector_query.embed_question(question)
    except Exception as e:
        warnings.append(f"vector leg unavailable: {e}")
    params = {"domains": expanded, "pool": POOL}

    rankings: dict[str, list[str]] = {}
    doc_units: dict[str, list] = {}
    passage_hits: dict[str, int] = {}
    chunk_summary: dict[str, dict] = {}
    chunk_summary_hits: dict[str, int] = {}
    with read_session() as session:
        if "P" in legs:
            try:
                vec = (vector_query._rows(session, vector_query._passage_vector_cypher(None),
                                          {**params, "embedding": embedding}) if embedding is not None else [])
                text = (vector_query._rows(session, vector_query._passage_text_cypher(None),
                                           {**params, "ft_query": ft_query}) if ft_query else [])
            except ClientError as e:
                if vector_query._is_missing_index(e):
                    raise ValueError(vector_query.MISSING_INDEX) from e
                raise
            order: list[str] = []
            for unit in retrieval_rank.passage_fuse(vec, text):
                if unit.kind != "chunk" or not unit.doc_id:
                    continue
                if unit.doc_id not in doc_units:
                    order.append(unit.doc_id)
                    doc_units[unit.doc_id] = []
                doc_units[unit.doc_id].append(unit)
                passage_hits[unit.doc_id] = passage_hits.get(unit.doc_id, 0) + unit.passages_matched
            rankings["P"] = order
        try:
            if "D" in legs:
                rankings["D"] = retrieval_rank.first_per_doc(
                    _summary_leg(session, "document", embedding, ft_query, params))
            if "C" in legs:
                fused = _summary_leg(session, "chunk", embedding, ft_query, params)
                for item in fused:
                    doc_id = item["row"].get("doc_id")
                    if doc_id:
                        chunk_summary_hits[doc_id] = chunk_summary_hits.get(doc_id, 0) + 1
                        chunk_summary.setdefault(doc_id, item["row"])
                rankings["C"] = retrieval_rank.first_per_doc(fused)
        except ClientError as e:
            if _is_missing_summary_index(e):
                raise ValueError(MISSING_SUMMARY_INDEX) from e
            raise
        picked = retrieval_rank.survey_fuse(rankings, weights=retrieval_rank.survey_leg_weights())[:top_docs]
        ids = [doc_id for doc_id, _, _ in picked]
        meta = ({r["doc_id"]: r for r in (serialize_record(x) for x in session.run(_DOCUMENTS, docIds=ids))}
                if ids else {})
        coverage = [serialize_record(r) for r in session.run(_COVERAGE, domains=expanded)]

    for row in coverage:
        missing = int(row.get("total") or 0) - int(row.get("summarised") or 0)
        if missing > 0:
            warnings.append(f"summaries missing for {missing} of {row['total']} documents in {row['domain']}; "
                            f"run artmind ingest summarise --domain {row['domain']}")

    documents = []
    for doc_id, score, ranks in picked:
        m = meta.get(doc_id) or {}
        best = chunk_summary.get(doc_id)
        documents.append({
            "doc_id": doc_id, "title": m.get("title"), "name": m.get("name"), "domain": m.get("domain"),
            "summary": m.get("summary"), "score": round(score, 6), "ranks": ranks,
            "hits": {"passages": passage_hits.get(doc_id, 0), "chunk_summaries": chunk_summary_hits.get(doc_id, 0)},
            "passages": [
                {"id": u.best["id"], "chunk_id": u.best["chunk_id"], "text": u.best.get("text"),
                 "relevance": None if u.relevance is None else round(u.relevance, 4)}
                for u in doc_units.get(doc_id, [])[:passages]
            ],
            "chunk_summary": {"chunk_id": best.get("chunk_id"), "text": best.get("text")} if best else None,
        })
    found = topics(ids, domains) if topics else []
    warnings.extend(getattr(found, "warnings", ()))
    return {
        **_domain_output(domains),
        "query_type": "survey",
        "question": question,
        "parameters": {"topDocs": top_docs, "passages": passages, "pool": POOL, "legs": list(legs)},
        "warnings": warnings,
        "documents": documents,
        "topics": list(found),
    }
