from loguru import logger
from neo4j.exceptions import ClientError

from artmind.extraction import embed_text as _embed_text
from artmind.graph_query import (
    read_session,
    resolve_as_of,
    sanitize_lucene_query,
    serialize_record,
    strip_internal_props,
)
from utils.functions import load_env


def _rrf_combine(vector_rows: list, text_rows: list, topK: int, k: int = 60) -> list:
    """Combine vector and full-text search results using Reciprocal Rank Fusion.

    RRF assigns a score to each result based on its rank in each ranking list:
    score(d) = sum(1 / (k + rank(d))) across all ranking systems

    Args:
        vector_rows: Results from vector search, already ranked
        text_rows: Results from full-text search, already ranked
        topK: Number of final results to return
        k: Constant for RRF formula (default 60)

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
    return [item["data"] for item in combined]


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
    resolved_model = model or _embedding_model()
    return _embed_text(resolved_model, question)


def vector_search(domains, question: str, topK: int = 5, as_of: str | None = None) -> dict:
    from artmind.graph_query import normalize_domains, domain_predicate, asof_predicate, _domain_output

    domains = normalize_domains(domains)
    as_of = resolve_as_of(as_of)
    embedding = embed_question(question)
    n = len(domains)

    # UserChat has no valid_from/valid_to per the schema design (chats aren't
    # versioned documents), so the asOf filter is only applied to the chunk
    # (document content) leg, the primary content search.
    asof_chunk = f"\n      AND {asof_predicate('node')}" if as_of else ""

    cypher_chunks = f"""
    CYPHER 25
    MATCH (node:DocChunk)
      SEARCH node IN (
        VECTOR INDEX chunk_embedding
        FOR $embedding
        LIMIT $candidateK
      )
    WHERE {domain_predicate("node")}{asof_chunk}
    WITH node, vector.similarity.cosine(node.embedding, $embedding) AS score
    OPTIONAL MATCH (node)-[:PART_OF]->(document:Document)
    RETURN score,
           node {{ .id, .name, .doc_id, .text }} AS chunk,
           document {{ .id, .name, .path, .domain }} AS document,
           'document' AS source_type
    ORDER BY score DESC
    LIMIT $topK
    """

    cypher_chats = f"""
    CYPHER 25
    MATCH (node:UserChat)
      SEARCH node IN (
        VECTOR INDEX user_chat_embedding
        FOR $embedding
        LIMIT $candidateK
      )
    WHERE {domain_predicate("node")}
    WITH node, vector.similarity.cosine(node.embedding, $embedding) AS score
    RETURN score,
           node {{ .id, .raw_text, .domain, .created_by, .created_at }} AS chat,
           'user_chat' AS source_type
    ORDER BY score DESC
    LIMIT $topK
    """

    params = {
        "domains": domains,
        "embedding": embedding,
        "topK": int(topK),
        "candidateK": max(int(topK) * 5 * n, int(topK)),
        **({"asOf": as_of} if as_of else {}),
    }

    with read_session() as session:
        chunk_rows = [
            strip_internal_props(serialize_record(record))
            for record in session.run(cypher_chunks, **params)
        ]
        try:
            chat_rows = [
                strip_internal_props(serialize_record(record))
                for record in session.run(cypher_chats, **params)
            ]
        except ClientError as e:
            if "IndexNotFound" in str(e) or "index" in str(e).lower():
                chat_rows = []
            else:
                raise

    all_rows = sorted(chunk_rows + chat_rows, key=lambda r: r.get("score", 0), reverse=True)[:int(topK)]

    return {
        **_domain_output(domains),
        "query_type": "vector",
        "question": question,
        "parameters": {"topK": int(topK), **({"asOf": as_of} if as_of else {})},
        "rows": all_rows,
    }


def full_text_search(domains, question: str, topK: int = 5, as_of: str | None = None) -> dict:
    """Full-text (Lucene) search on DocChunk and UserChat text content.

    Uses the chunk_text_ft and user_chat_text_ft indexes created by
    `artmind setup`. Lucene handles tokenization, case folding, and BM25
    relevance ranking; terms are OR-combined so natural-language questions
    still match chunks containing only the salient words.
    """
    from artmind.graph_query import normalize_domains, domain_predicate, asof_predicate, _domain_output

    domains = normalize_domains(domains)
    as_of = resolve_as_of(as_of)
    query = sanitize_lucene_query(question)

    result: dict = {
        **_domain_output(domains),
        "query_type": "full_text",
        "question": question,
        "parameters": {"topK": int(topK), **({"asOf": as_of} if as_of else {})},
        "rows": [],
    }
    if not query:
        return result

    # UserChat has no valid_from/valid_to (see vector_search) — asOf only
    # applies to the chunk (document content) leg.
    asof_chunk = f"\n      AND {asof_predicate('node')}" if as_of else ""

    cypher_chunks = f"""
    CALL db.index.fulltext.queryNodes('chunk_text_ft', $ft_query)
    YIELD node, score
    WHERE {domain_predicate("node")}{asof_chunk}
    OPTIONAL MATCH (node)-[:PART_OF]->(document:Document)
    RETURN score,
           node {{ .id, .name, .doc_id, .text }} AS chunk,
           document {{ .id, .name, .path, .domain }} AS document,
           'document' AS source_type
    ORDER BY score DESC
    LIMIT $topK
    """

    cypher_chats = f"""
    CALL db.index.fulltext.queryNodes('user_chat_text_ft', $ft_query)
    YIELD node, score
    WHERE {domain_predicate("node")}
    RETURN score,
           node {{ .id, .raw_text, .domain, .created_by, .created_at }} AS chat,
           'user_chat' AS source_type
    ORDER BY score DESC
    LIMIT $topK
    """

    params = {
        "domains": domains,
        "ft_query": query,
        "topK": int(topK),
        **({"asOf": as_of} if as_of else {}),
    }

    with read_session() as session:
        chunk_rows = [
            strip_internal_props(serialize_record(record))
            for record in session.run(cypher_chunks, **params)
        ]
        try:
            chat_rows = [
                strip_internal_props(serialize_record(record))
                for record in session.run(cypher_chats, **params)
            ]
        except ClientError as e:
            if "IndexNotFound" in str(e) or "index" in str(e).lower():
                chat_rows = []
            else:
                raise

    result["rows"] = sorted(
        chunk_rows + chat_rows, key=lambda r: r.get("score", 0), reverse=True
    )[: int(topK)]
    return result


def entity_resolve(domains, reference: str, topK: int = 5) -> dict:
    """Resolve a free-text entity reference to canonical graph entities.

    Combines Lucene full-text over entity name+description (entity_name_ft)
    with vector similarity over entity embeddings (entity_embedding) via RRF.
    The fulltext leg catches name fragments; the vector leg catches purely
    descriptive references ("the detective") that share no words with the name.

    No `--asOf` (Phase 4) — the projection is current by construction.
    """
    from artmind.graph_query import normalize_domains, domain_predicate, _domain_output

    domains = normalize_domains(domains)
    n = len(domains)
    ft_query = sanitize_lucene_query(reference)

    cypher_ft = f"""
    CALL db.index.fulltext.queryNodes('entity_name_ft', $ft_query)
    YIELD node AS e, score
    WHERE {domain_predicate("e", prop="_domain")}
    RETURN score,
           e {{ ._id, .name, .entity_class, .type, .description, ._domain, label: labels(e) }} AS entity
    ORDER BY score DESC
    LIMIT $topK
    """

    cypher_vec = f"""
    CYPHER 25
    MATCH (node:Entity)
      SEARCH node IN (
        VECTOR INDEX entity_embedding
        FOR $embedding
        LIMIT $candidateK
      )
    WHERE {domain_predicate("node", prop="_domain")}
    WITH node, vector.similarity.cosine(node.embedding, $embedding) AS score
    RETURN score,
           node {{ ._id, .name, .entity_class, .type, .description, ._domain, label: labels(node) }} AS entity
    ORDER BY score DESC
    LIMIT $topK
    """

    with read_session() as session:
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

    combined_rows = _rrf_combine(vec_rows, ft_rows, int(topK))

    return {
        **_domain_output(domains),
        "query_type": "entity_resolve",
        "question": reference,
        "parameters": {"topK": int(topK)},
        "rows": combined_rows,
    }


def vector_text_search(domains, question: str, topK: int = 5, as_of: str | None = None) -> dict:
    """Combined vector and full-text search using Reciprocal Rank Fusion.

    Searches both semantic embeddings (vector) and keyword matches (full-text),
    then combines results using RRF to balance both relevance signals.
    Returns results ranked by combined RRF score.
    """
    from artmind.graph_query import normalize_domains, _domain_output

    domains = normalize_domains(domains)
    as_of = resolve_as_of(as_of)

    # Run both searches in parallel (conceptually - sequentially in practice).
    # as_of is passed only when set so callers/mocks with the pre-existing
    # (domain, question, topK) signature keep working unchanged.
    asof_kwargs = {"as_of": as_of} if as_of else {}
    vector_results = vector_search(domains, question, topK, **asof_kwargs)
    text_results = full_text_search(domains, question, topK, **asof_kwargs)

    # Combine using RRF
    combined_rows = _rrf_combine(vector_results["rows"], text_results["rows"], topK)

    return {
        **_domain_output(domains),
        "query_type": "vector_text",
        "question": question,
        "parameters": {"topK": int(topK), **({"asOf": as_of} if as_of else {})},
        "rows": combined_rows,
    }
