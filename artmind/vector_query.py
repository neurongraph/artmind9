import ollama
from loguru import logger
from neo4j.exceptions import ClientError

from artmind.graph_query import (
    neo4j_session,
    sanitize_lucene_query,
    serialize_record,
    strip_embeddings,
)
from utils.functions import load_env, log_llm_call


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
        return f"entity:{row['entity']['id']}"
    return str(id(row))


def _embedding_model() -> str:
    env = load_env()
    return env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")


def embed_question(question: str, model: str | None = None) -> list[float]:
    resolved_model = model or _embedding_model()
    response = ollama.embed(model=resolved_model, input=question)
    embedding = response.embeddings[0]
    log_llm_call("embed", resolved_model, question, f"[embedding vector, dim={len(embedding)}]")
    return embedding


def vector_search(domain: str, question: str, topK: int = 5) -> dict:
    embedding = embed_question(question)

    cypher_chunks = """
    CYPHER 25
    MATCH (node:DocChunk)
      SEARCH node IN (
        VECTOR INDEX chunk_embedding
        FOR $embedding
        LIMIT $candidateK
      )
    WHERE (node.domain = $domain OR node.domain STARTS WITH ($domain + '.'))
    WITH node, vector.similarity.cosine(node.embedding, $embedding) AS score
    OPTIONAL MATCH (node)-[:PART_OF]->(document:Document)
    RETURN score,
           node { .id, .name, .doc_id, .text } AS chunk,
           document { .id, .name, .path, .domain } AS document,
           'document' AS source_type
    ORDER BY score DESC
    LIMIT $topK
    """

    cypher_chats = """
    CYPHER 25
    MATCH (node:UserChat)
      SEARCH node IN (
        VECTOR INDEX user_chat_embedding
        FOR $embedding
        LIMIT $candidateK
      )
    WHERE (node.domain = $domain OR node.domain STARTS WITH ($domain + '.'))
    WITH node, vector.similarity.cosine(node.embedding, $embedding) AS score
    RETURN score,
           node { .id, .raw_text, .domain, .created_by, .created_at } AS chat,
           'user_chat' AS source_type
    ORDER BY score DESC
    LIMIT $topK
    """

    params = {
        "domain": domain,
        "embedding": embedding,
        "topK": int(topK),
        "candidateK": max(int(topK) * 5, int(topK)),
    }

    with neo4j_session() as session:
        chunk_rows = [
            strip_embeddings(serialize_record(record))
            for record in session.run(cypher_chunks, **params)
        ]
        try:
            chat_rows = [
                strip_embeddings(serialize_record(record))
                for record in session.run(cypher_chats, **params)
            ]
        except ClientError as e:
            if "IndexNotFound" in str(e) or "index" in str(e).lower():
                chat_rows = []
            else:
                raise

    all_rows = sorted(chunk_rows + chat_rows, key=lambda r: r.get("score", 0), reverse=True)[:int(topK)]

    return {
        "domain": domain,
        "query_type": "vector",
        "question": question,
        "parameters": {"topK": int(topK)},
        "rows": all_rows,
    }


def full_text_search(domain: str, question: str, topK: int = 5) -> dict:
    """Full-text (Lucene) search on DocChunk and UserChat text content.

    Uses the chunk_text_ft and user_chat_text_ft indexes created by
    `artmind setup`. Lucene handles tokenization, case folding, and BM25
    relevance ranking; terms are OR-combined so natural-language questions
    still match chunks containing only the salient words.
    """
    query = sanitize_lucene_query(question)

    result: dict = {
        "domain": domain,
        "query_type": "full_text",
        "question": question,
        "parameters": {"topK": int(topK)},
        "rows": [],
    }
    if not query:
        return result

    cypher_chunks = """
    CALL db.index.fulltext.queryNodes('chunk_text_ft', $ft_query)
    YIELD node, score
    WHERE (node.domain = $domain OR node.domain STARTS WITH ($domain + '.'))
    OPTIONAL MATCH (node)-[:PART_OF]->(document:Document)
    RETURN score,
           node { .id, .name, .doc_id, .text } AS chunk,
           document { .id, .name, .path, .domain } AS document,
           'document' AS source_type
    ORDER BY score DESC
    LIMIT $topK
    """

    cypher_chats = """
    CALL db.index.fulltext.queryNodes('user_chat_text_ft', $ft_query)
    YIELD node, score
    WHERE (node.domain = $domain OR node.domain STARTS WITH ($domain + '.'))
    RETURN score,
           node { .id, .raw_text, .domain, .created_by, .created_at } AS chat,
           'user_chat' AS source_type
    ORDER BY score DESC
    LIMIT $topK
    """

    params = {
        "domain": domain,
        "ft_query": query,
        "topK": int(topK),
    }

    with neo4j_session() as session:
        chunk_rows = [
            strip_embeddings(serialize_record(record))
            for record in session.run(cypher_chunks, **params)
        ]
        try:
            chat_rows = [
                strip_embeddings(serialize_record(record))
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


def entity_resolve(domain: str, reference: str, topK: int = 5) -> dict:
    """Resolve a free-text entity reference to canonical graph entities.

    Combines Lucene full-text over entity name+description (entity_name_ft)
    with vector similarity over entity embeddings (entity_embedding) via RRF.
    The fulltext leg catches name fragments; the vector leg catches purely
    descriptive references ("the detective") that share no words with the name.
    """
    ft_query = sanitize_lucene_query(reference)

    cypher_ft = """
    CALL db.index.fulltext.queryNodes('entity_name_ft', $ft_query)
    YIELD node AS e, score
    WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
    RETURN score,
           e { .id, .name, .entity_class, .type, .description, .domain, label: labels(e) } AS entity
    ORDER BY score DESC
    LIMIT $topK
    """

    cypher_vec = """
    CYPHER 25
    MATCH (node:Entity)
      SEARCH node IN (
        VECTOR INDEX entity_embedding
        FOR $embedding
        LIMIT $candidateK
      )
    WHERE (node.domain = $domain OR node.domain STARTS WITH ($domain + '.'))
    WITH node, vector.similarity.cosine(node.embedding, $embedding) AS score
    RETURN score,
           node { .id, .name, .entity_class, .type, .description, .domain, label: labels(node) } AS entity
    ORDER BY score DESC
    LIMIT $topK
    """

    with neo4j_session() as session:
        ft_rows: list = []
        if ft_query:
            ft_rows = [
                strip_embeddings(serialize_record(record))
                for record in session.run(
                    cypher_ft, domain=domain, ft_query=ft_query, topK=int(topK)
                )
            ]

        vec_rows: list = []
        try:
            embedding = embed_question(reference)
            vec_rows = [
                strip_embeddings(serialize_record(record))
                for record in session.run(
                    cypher_vec,
                    domain=domain,
                    embedding=embedding,
                    topK=int(topK),
                    candidateK=max(int(topK) * 5, int(topK)),
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
        "domain": domain,
        "query_type": "entity_resolve",
        "question": reference,
        "parameters": {"topK": int(topK)},
        "rows": combined_rows,
    }


def vector_text_search(domain: str, question: str, topK: int = 5) -> dict:
    """Combined vector and full-text search using Reciprocal Rank Fusion.

    Searches both semantic embeddings (vector) and keyword matches (full-text),
    then combines results using RRF to balance both relevance signals.
    Returns results ranked by combined RRF score.
    """
    # Run both searches in parallel (conceptually - sequentially in practice)
    vector_results = vector_search(domain, question, topK)
    text_results = full_text_search(domain, question, topK)

    # Combine using RRF
    combined_rows = _rrf_combine(vector_results["rows"], text_results["rows"], topK)

    return {
        "domain": domain,
        "query_type": "vector_text",
        "question": question,
        "parameters": {"topK": int(topK)},
        "rows": combined_rows,
    }
