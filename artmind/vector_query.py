from loguru import logger
from neo4j.exceptions import ClientError

from artmind import extraction
from artmind.graph_query import (
    read_session,
    resolve_as_of,
    sanitize_lucene_query,
    serialize_record,
    strip_internal_props,
    supersedes_field,
)
from utils.functions import load_env


def _rrf_combine(
    vector_rows: list, text_rows: list, topK: int, k: int = 60, *, annotate: bool = False
) -> list:
    """Combine vector and full-text search results using Reciprocal Rank Fusion.

    RRF assigns a score to each result based on its rank in each ranking list:
    score(d) = sum(1 / (k + rank(d))) across all ranking systems

    Args:
        vector_rows: Results from vector search, already ranked
        text_rows: Results from full-text search, already ranked
        topK: Number of final results to return
        k: Constant for RRF formula (default 60)
        annotate: Replace each row's leg-specific `score` with the fused RRF
            score and add `matched_by` (the legs that found it). The legs'
            raw scores are on different scales (cosine 0-1, BM25 unbounded),
            so a caller comparing them is misled -- a weak keyword hit can
            outscore the strong semantic match ranked above it.

    Returns:
        Combined and reranked results
    """
    # Build a map of result ID -> result data and accumulate RRF scores
    result_map = {}

    # Process vector results
    for rank, row in enumerate(vector_rows, start=1):
        result_id = _get_result_id(row)
        if result_id not in result_map:
            result_map[result_id] = {
                "data": row,
                "rrf_score": 0.0,
                "vector_rank": rank,
                "text_rank": None,
            }
        # Add RRF contribution from vector ranking
        result_map[result_id]["rrf_score"] += 1.0 / (k + rank)

    # Process text results
    for rank, row in enumerate(text_rows, start=1):
        result_id = _get_result_id(row)
        if result_id not in result_map:
            result_map[result_id] = {
                "data": row,
                "rrf_score": 0.0,
                "vector_rank": None,
                "text_rank": rank,
            }
        else:
            result_map[result_id]["text_rank"] = rank
        # Add RRF contribution from text ranking
        result_map[result_id]["rrf_score"] += 1.0 / (k + rank)

    # Sort by RRF score and return top K
    combined = sorted(result_map.values(), key=lambda x: x["rrf_score"], reverse=True)[:topK]
    if not annotate:
        return [item["data"] for item in combined]
    return [
        {
            **item["data"],
            "score": round(item["rrf_score"], 6),
            "matched_by": [
                leg
                for leg, rank in (("vector", item["vector_rank"]), ("fulltext", item["text_rank"]))
                if rank is not None
            ],
        }
        for item in combined
    ]


def _put_exact_first(exact_rows: list, combined_rows: list, top_k: int, k: int = 60) -> list:
    """Put exact-name hits ahead of the fused rows, tagged `exact` in `matched_by`.

    An entity that is also in the fused rows keeps its own legs and RRF score;
    one that only the exact leg found gets the score of a rank-1 hit. Never
    shorter than the exact hits, so every one of them survives the top-k cut.
    """
    if not exact_rows:
        return combined_rows
    by_id = {r["entity"]["_id"]: r for r in combined_rows if r.get("entity")}
    head: list = []
    for row in exact_rows:
        found = by_id.get(row["entity"]["_id"])
        if found is not None:
            head.append({**found, "matched_by": ["exact", *found["matched_by"]]})
        else:
            head.append({**row, "score": round(1.0 / (k + 1), 6), "matched_by": ["exact"]})
    exact_ids = {r["entity"]["_id"] for r in head}
    tail = [r for r in combined_rows if r.get("entity") and r["entity"]["_id"] not in exact_ids]
    return (head + tail)[: max(top_k, len(head))]


def _get_result_id(row: dict) -> str:
    """Extract a unique ID from a result row (chunk, chat, or entity)."""
    if "chunk" in row and row["chunk"]:
        return f"chunk:{row['chunk']['id']}"
    elif "chat" in row and row["chat"]:
        return f"chat:{row['chat']['id']}"
    elif "entity" in row and row["entity"]:
        return f"entity:{row['entity']['_id']}"
    return str(id(row))


def _embedding_model() -> str:
    env = load_env()
    return env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")


def embed_question(question: str, model: str | None = None) -> list[float]:
    return extraction.embed_query(model or _embedding_model(), question)


def _entity_ft_cypher() -> str:
    from artmind.graph_query import domain_predicate

    return f"""
    CALL db.index.fulltext.queryNodes('entity_name_ft', $ft_query)
    YIELD node AS e, score
    WHERE {domain_predicate("e")}
    RETURN score,
           e {{ ._id, .name, .entity_class, .type, .description, ._domain, label: labels(e),
                observation_count: e._observation_count,
                connection_count: COUNT {{ (e)-[:RELATES_TO]-(:Entity) }} }} AS entity
    ORDER BY score DESC
    LIMIT $topK
    """


def _entity_exact_cypher() -> str:
    from artmind.graph_query import domain_predicate

    return f"""
    MATCH (e:Entity)
    WHERE {domain_predicate("e")} AND toLower(e.name) = toLower($reference)
    RETURN e {{ ._id, .name, .entity_class, .type, .description, ._domain, label: labels(e),
                observation_count: e._observation_count,
                connection_count: COUNT {{ (e)-[:RELATES_TO]-(:Entity) }} }} AS entity
    ORDER BY e._observation_count DESC
    LIMIT $topK
    """


def entity_name_candidates(domains, reference: str, topK: int = 10) -> dict:
    """Entities a reference names, from the exact and fulltext legs only.

    No embedding call and no RRF: an exact-name hit comes first, then the
    fulltext hits by score, de-duplicated by `_id`. Rows are shaped like
    `entity_resolve`'s (`entity`, `matched_by`, `score`), so a caller can read
    both; use this where a vector neighbour must not pass for evidence.
    """
    from artmind.graph_query import normalize_domains, _domain_output

    domains = normalize_domains(domains)
    ft_query = sanitize_lucene_query(reference)
    top_k = int(topK)
    by_id: dict = {}

    def add(rows: list, leg: str) -> None:
        for row in rows:
            entity = row.get("entity")
            if not entity:
                continue
            found = by_id.get(entity.get("_id"))
            if found is None:
                by_id[entity.get("_id")] = {
                    **row, "score": row.get("score", 1.0), "matched_by": [leg],
                }
            elif leg not in found["matched_by"]:
                found["matched_by"].append(leg)

    with read_session() as session:
        if reference.strip():
            add([strip_internal_props(serialize_record(r)) for r in session.run(
                _entity_exact_cypher(), domains=domains, reference=reference.strip(), topK=top_k)], "exact")
        if ft_query:
            add([strip_internal_props(serialize_record(r)) for r in session.run(
                _entity_ft_cypher(), domains=domains, ft_query=ft_query, topK=top_k)], "fulltext")
    rows = list(by_id.values())
    n_exact = sum(1 for r in rows if "exact" in r["matched_by"])
    return {
        **_domain_output(domains),
        "query_type": "entity_name_candidates",
        "question": reference,
        "parameters": {"topK": top_k},
        "rows": rows[: max(top_k, n_exact)],
    }


def entity_resolve(domains, reference: str, topK: int = 5) -> dict:
    """Resolve a free-text entity reference to canonical graph entities.

    Combines Lucene full-text over entity name+description (entity_name_ft)
    with vector similarity over entity embeddings (entity_embedding) via RRF.
    The fulltext leg catches name fragments; the vector leg catches purely
    descriptive references ("the detective") that share no words with the name.

    Each hit carries `observation_count` and `connection_count` -- how much
    the entity has aggregated -- so a caller can tell a hub (hundreds of
    observations) from a passing mention before asking for its context, and
    `score` is the fused RRF score with `matched_by` naming the legs.

    A third leg, `exact`, finds entities whose name equals the reference
    (case-insensitive) and always ranks them first. Lucene scores a short,
    one-off name like "Sonography for X" above the hub entity "X" itself, and
    RRF then drops an fulltext-only hit from the cut, so without it a person
    with hundreds of observations is not returned by their own name.

    No `--asOf` (Phase 4) — the projection is current by construction.
    """
    from artmind.graph_query import normalize_domains, domain_predicate, _domain_output

    domains = normalize_domains(domains)
    n = len(domains)
    ft_query = sanitize_lucene_query(reference)

    cypher_ft = _entity_ft_cypher()

    cypher_vec = f"""
    CYPHER 25
    MATCH (node:Entity)
      SEARCH node IN (
        VECTOR INDEX entity_embedding
        FOR $embedding
        LIMIT $candidateK
      )
    WHERE {domain_predicate("node")}
    WITH node, vector.similarity.cosine(node.embedding, $embedding) AS score
    RETURN score,
           node {{ ._id, .name, .entity_class, .type, .description, ._domain, label: labels(node),
                   observation_count: node._observation_count,
                   connection_count: COUNT {{ (node)-[:RELATES_TO]-(:Entity) }} }} AS entity
    ORDER BY score DESC
    LIMIT $topK
    """

    cypher_exact = _entity_exact_cypher()

    with read_session() as session:
        exact_rows: list = []
        if reference.strip():
            exact_rows = [
                strip_internal_props(serialize_record(record))
                for record in session.run(
                    cypher_exact, domains=domains, reference=reference.strip(), topK=int(topK)
                )
            ]

        ft_rows: list = []
        if ft_query:
            ft_rows = [
                strip_internal_props(serialize_record(record))
                for record in session.run(
                    cypher_ft, domains=domains, ft_query=ft_query, topK=int(topK)
                )
            ]

        vec_rows: list = []
        try:
            embedding = embed_question(reference)
            vec_rows = [
                strip_internal_props(serialize_record(record))
                for record in session.run(
                    cypher_vec,
                    domains=domains,
                    embedding=embedding,
                    topK=int(topK),
                    candidateK=max(int(topK) * 5 * n, int(topK)),
                )
            ]
        except ClientError as e:
            # entity_embedding index missing (pre-existing graph not yet
            # backfilled) — fulltext leg alone still resolves most names
            if "IndexNotFound" in str(e) or "index" in str(e).lower():
                vec_rows = []
            else:
                raise
        except Exception as e:
            logger.warning("entity-resolve vector leg unavailable: {}", e)
            vec_rows = []

    combined_rows = _rrf_combine(vec_rows, ft_rows, int(topK), annotate=True)
    combined_rows = _put_exact_first(exact_rows, combined_rows, int(topK))

    return {
        **_domain_output(domains),
        "query_type": "entity_resolve",
        "question": reference,
        "parameters": {"topK": int(topK)},
        "rows": combined_rows,
    }



from artmind import brief, retrieval_rank

POOL_MIN = 50
MAX_TOP_K = 50
CONTEXTS = ("chunk", "passage")
MISSING_INDEX = (
    "the passage index is missing: this graph has not been migrated to passages. Run "
    "`artmind ingest embed-passages` (or `artmind vault sync`) inside the vault, then `artmind setup`."
)

_PASSAGE_ROW = ("kind: 'passage', id: p.id, chunk_id: p.chunk_id, doc_id: p.doc_id, "
                "char_start: p.char_start, char_end: p.char_end, text: p.text")
_CHAT_ROW = "kind: 'chat', id: c.id, chat: c { .id, .raw_text, ._domain, .created_by, .created_at }"


def _pool(top_k: int) -> int:
    return max(POOL_MIN, 10 * top_k)


def _expand(domains: list[str]) -> list[str]:
    from artmind.graph_query import expand_domain_family

    out: list[str] = []
    for d in domains:
        for member in expand_domain_family(d):
            if member not in out:
                out.append(member)
    return out


def _passage_vector_cypher(as_of) -> str:
    from artmind.graph_query import asof_predicate

    asof = f"\n    WHERE {asof_predicate('p')}" if as_of else ""
    return f"""
    CYPHER 25
    MATCH (p:DocPassage)
      SEARCH p IN (
        VECTOR INDEX passage_embedding
        FOR $embedding
        WHERE p._domain IN $domains
        LIMIT $pool
      )
    WITH p, vector.similarity.cosine(p.embedding, $embedding) AS score{asof}
    RETURN p {{ {_PASSAGE_ROW}, cosine: score }} AS row
    ORDER BY score DESC
    """


def _passage_text_cypher(as_of) -> str:
    from artmind.graph_query import asof_predicate

    asof = f" AND {asof_predicate('p')}" if as_of else ""
    return f"""
    CALL db.index.fulltext.queryNodes('passage_text_ft', $ft_query)
    YIELD node AS p, score
    WHERE p._domain IN $domains{asof}
    RETURN p {{ {_PASSAGE_ROW} }} AS row
    ORDER BY score DESC
    LIMIT $pool
    """


def _chat_vector_cypher() -> str:
    from artmind.graph_query import domain_predicate

    return f"""
    CYPHER 25
    MATCH (c:UserChat)
      SEARCH c IN (VECTOR INDEX user_chat_embedding FOR $embedding LIMIT $pool)
    WITH c, vector.similarity.cosine(c.embedding, $embedding) AS score
    WHERE {domain_predicate("c")}
    RETURN c {{ {_CHAT_ROW}, cosine: score }} AS row
    ORDER BY score DESC
    """


def _chat_text_cypher() -> str:
    from artmind.graph_query import domain_predicate

    return f"""
    CALL db.index.fulltext.queryNodes('user_chat_text_ft', $ft_query)
    YIELD node AS c, score
    WHERE {domain_predicate("c")}
    RETURN c {{ {_CHAT_ROW} }} AS row
    ORDER BY score DESC
    LIMIT $pool
    """


def _chunk_fetch_cypher() -> str:
    return f"""
    UNWIND $chunkIds AS cid
    MATCH (c:DocChunk {{id: cid}})
    OPTIONAL MATCH (c)-[:PART_OF]->(document:Document)
    RETURN c {{ .id, .name, .doc_id, .header_path, .text }} AS chunk,
           document {{ .id, .name, .path, .source_path, ._domain, {supersedes_field("document")},
                      title: coalesce(document.title, document.name), date: coalesce(document._valid_from, document.valid_from),
                      superseded: EXISTS {{ MATCH (:Document)-[:SUPERSEDES]->(document) }} }} AS document
    """


def _rows(session, cypher: str, params: dict) -> list[dict]:
    return [serialize_record(r)["row"] for r in session.run(cypher, **params)]


def _optional_rows(session, cypher: str, params: dict) -> list[dict]:
    """A chat leg: its index may be absent on an old graph (no chats ever)."""
    try:
        return _rows(session, cypher, params)
    except ClientError as e:
        if "index" in str(e).lower():
            return []
        raise


def _is_missing_index(e: ClientError) -> bool:
    text = str(e).lower()
    return "passage_embedding" in text or "passage_text_ft" in text or ("no such" in text and "index" in text)


def _with_status(document):
    """The fetched document with `status` (and without the raw `superseded` flag)."""
    if not document:
        return document
    out = {k: v for k, v in document.items() if k != "superseded"}
    out["status"] = brief.doc_status(document.get("superseded"))
    return out


def _second_passages(units, vector_rows, text_rows) -> dict[str, str]:
    """Text of each picked chunk's second-best passage (RRF over both legs), for brief rows."""
    best_ids = {u.best["chunk_id"]: u.best["id"] for u in units if u.kind == "chunk"}
    scored: dict[str, list] = {}          # passage id -> [score, chunk id, text]
    for leg_rows in (vector_rows, text_rows):
        for rank, row in enumerate(leg_rows, 1):
            cid = row.get("chunk_id")
            if row.get("kind") != "passage" or cid not in best_ids or row["id"] == best_ids[cid]:
                continue
            entry = scored.setdefault(row["id"], [0.0, cid, row.get("text") or ""])
            entry[0] += 1.0 / (retrieval_rank.RRF_K + rank)
    out: dict[str, list] = {}
    for score, cid, text in scored.values():
        if cid not in out or score > out[cid][0]:
            out[cid] = [score, text]
    return {cid: text for cid, (_, text) in out.items()}


def vector_text_search(
    domains,
    question: str,
    topK: int = 5,
    as_of: str | None = None,
    *,
    context: str = "chunk",
    shape: str = "full",
) -> dict:
    """Hybrid search over passages (spec 2026-10-31 §4.2): vector (domain-filtered
    inside SEARCH) + BM25, each over a deep pool; RRF per passage; MaxP into parent
    chunks; the top K parent chunks (or passages) with the matching span."""
    from artmind.graph_query import _domain_output, normalize_domains

    top_k = int(topK)
    if not 1 <= top_k <= MAX_TOP_K:
        raise ValueError(f"--topK must be between 1 and {MAX_TOP_K}")
    if context not in CONTEXTS:
        raise ValueError(f"--context must be one of {', '.join(CONTEXTS)}")
    if shape not in brief.SHAPES:
        raise ValueError(f"--shape must be one of {', '.join(brief.SHAPES)}")
    if shape == "brief":
        context = "passage"
    domains = normalize_domains(domains)
    as_of = resolve_as_of(as_of)
    pool = _pool(top_k)
    warnings: list[str] = []
    ft_query = sanitize_lucene_query(question)

    embedding = None
    try:
        embedding = embed_question(question)
    except Exception as e:
        warnings.append(f"vector leg unavailable: {e}")

    params = {"domains": _expand(domains), "pool": pool, **({"asOf": as_of} if as_of else {})}
    chat_params = {"domains": domains, "pool": pool}
    vector_rows: list[dict] = []
    text_rows: list[dict] = []
    with read_session() as session:
        try:
            if embedding is not None:
                vector_rows = _rows(session, _passage_vector_cypher(as_of), {**params, "embedding": embedding})
                vector_rows += _optional_rows(session, _chat_vector_cypher(), {**chat_params, "embedding": embedding})
            if ft_query:
                text_rows = _rows(session, _passage_text_cypher(as_of), {**params, "ft_query": ft_query})
                text_rows += _optional_rows(session, _chat_text_cypher(), {**chat_params, "ft_query": ft_query})
        except ClientError as e:
            if _is_missing_index(e):
                raise ValueError(MISSING_INDEX) from e
            raise
        # A chat's rows were appended after the passage rows; rank each leg by its own score.
        vector_rows.sort(key=lambda r: -(r.get("cosine") or 0.0))
        units = retrieval_rank.passage_fuse(vector_rows, text_rows)
        picked = units[:top_k]
        chunk_ids = [u.best["chunk_id"] for u in picked if u.kind == "chunk"]
        fetched = {}
        if chunk_ids:
            for r in session.run(_chunk_fetch_cypher(), chunkIds=chunk_ids):
                rec = serialize_record(r)
                fetched[rec["chunk"]["id"]] = rec

    rows = []
    second_text = _second_passages(picked, vector_rows, text_rows) if shape == "brief" else {}
    for u in picked:
        common = {"score": round(u.score, 6), "relevance": None if u.relevance is None else round(u.relevance, 4),
                  "matched_by": u.matched_by}
        if u.kind == "chat":
            rows.append({"chat": u.best["chat"], "source_type": "user_chat", **common})
            continue
        rec = fetched.get(u.best["chunk_id"])
        if rec is None:            # chunk vanished between the two reads
            continue
        chunk = dict(rec["chunk"])
        document = _with_status(rec.get("document"))
        if shape == "brief":
            ref = brief.doc_ref((document or {}).get("id"), (document or {}).get("title"), (document or {}).get("name"),
                                (document or {}).get("_domain"),
                                (document or {}).get("date"), (document or {}).get("status") == "superseded",
                                (document or {}).get("supersedes"))
            row = brief.passage_row(len(rows) + 1, u.best["id"], chunk.get("id"), u.best.get("text"),
                                    common["relevance"], ref,
                                    second=second_text.get(u.best["chunk_id"]) if u.passages_matched >= 2 else None)
            row["score"] = common["score"]
            row["matched_by"] = common["matched_by"]
            row["passages_matched"] = u.passages_matched
            rows.append(row)
            continue
        if context == "passage":
            chunk["text"] = u.best.get("text")
        rows.append({
            "chunk": chunk, "document": document, "source_type": "document", **common,
            "passage": {"id": u.best["id"], "char_start": u.best.get("char_start"), "char_end": u.best.get("char_end")},
            "passages_matched": u.passages_matched,
        })

    return {
        **_domain_output(domains),
        "query_type": "vector_text",
        "question": question,
        "parameters": {"topK": top_k, "pool": pool, "context": context,
                       **({"shape": "brief"} if shape == "brief" else {}),
                       **({"asOf": as_of} if as_of else {})},
        "warnings": warnings,
        "rows": rows,
    }
