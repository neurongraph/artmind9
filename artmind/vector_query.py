import ollama
from neo4j.exceptions import ClientError

from artmind.graph_query import neo4j_session, serialize_record, strip_embeddings
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
    """Extract a unique ID from a result row (chunk or chat)."""
    if "chunk" in row and row["chunk"]:
        return f"chunk:{row['chunk']['id']}"
    elif "chat" in row and row["chat"]:
        return f"chat:{row['chat']['id']}"
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
    """Full-text search on DocChunk and UserChat text content.

    Keyword matching fallback when vector search returns sparse results.
    Returns both exact phrase matches and individual keyword matches ranked by relevance.
    """
    # Build search query with AND for multi-word phrases and OR for individual words
    search_terms = question.split()
    # Create a simple keyword match: all words should appear in the text
    keyword_conditions = " AND ".join([f"(node.text CONTAINS $word{i})" for i in range(len(search_terms))])
    keyword_params = {f"word{i}": term.lower() for i, term in enumerate(search_terms)}

    cypher_chunks = f"""
    MATCH (node:DocChunk)
    WHERE (node.domain = $domain OR node.domain STARTS WITH ($domain + '.')) AND {keyword_conditions}
    OPTIONAL MATCH (node)-[:PART_OF]->(document:Document)
    WITH node, document,
         apoc.text.levenshteinSimilarity(node.text, $question) AS relevance
    RETURN relevance AS score,
           node {{ .id, .name, .doc_id, .text }} AS chunk,
           document {{ .id, .name, .path, .domain }} AS document,
           'document' AS source_type
    ORDER BY relevance DESC
    LIMIT $topK
    """

    cypher_chats = f"""
    MATCH (node:UserChat)
    WHERE (node.domain = $domain OR node.domain STARTS WITH ($domain + '.')) AND {keyword_conditions}
    WITH node,
         apoc.text.levenshteinSimilarity(node.raw_text, $question) AS relevance
    RETURN relevance AS score,
           node {{ .id, .raw_text, .domain, .created_by, .created_at }} AS chat,
           'user_chat' AS source_type
    ORDER BY relevance DESC
    LIMIT $topK
    """

    params = {
        "domain": domain,
        "question": question,
        "topK": int(topK),
        **keyword_params,
    }

    with neo4j_session() as session:
        try:
            chunk_rows = [
                strip_embeddings(serialize_record(record))
                for record in session.run(cypher_chunks, **params)
            ]
        except ClientError as e:
            if "apoc" in str(e).lower() or "unknown function" in str(e).lower():
                # Fallback if APOC not available: simple substring matching
                chunk_rows = _full_text_fallback_chunks(session, domain, search_terms, int(topK))
            else:
                raise

        try:
            chat_rows = [
                strip_embeddings(serialize_record(record))
                for record in session.run(cypher_chats, **params)
            ]
        except ClientError as e:
            if "apoc" in str(e).lower() or "unknown function" in str(e).lower():
                chat_rows = _full_text_fallback_chats(session, domain, search_terms, int(topK))
            else:
                raise

    all_rows = sorted(chunk_rows + chat_rows, key=lambda r: r.get("score", 0), reverse=True)[:int(topK)]

    return {
        "domain": domain,
        "query_type": "full_text",
        "question": question,
        "parameters": {"topK": int(topK)},
        "rows": all_rows,
    }


def _full_text_fallback_chunks(session, domain: str, search_terms: list, topK: int) -> list:
    """Fallback full-text search using simple CONTAINS matching."""
    cypher = """
    MATCH (node:DocChunk)
    WHERE (node.domain = $domain OR node.domain STARTS WITH ($domain + '.'))
    OPTIONAL MATCH (node)-[:PART_OF]->(document:Document)
    RETURN node { .id, .name, .doc_id, .text } AS chunk,
           document { .id, .name, .path, .domain } AS document,
           'document' AS source_type
    LIMIT $limit
    """

    results = []
    for record in session.run(cypher, domain=domain, limit=topK * 5):
        chunk_text = record["chunk"]["text"].lower() if record["chunk"] else ""
        # Count matching terms
        matches = sum(1 for term in search_terms if term.lower() in chunk_text)
        if matches > 0:
            results.append({
                "score": matches / len(search_terms),  # Normalize by number of search terms
                "chunk": record["chunk"],
                "document": record["document"],
                "source_type": record["source_type"],
            })

    return sorted(results, key=lambda r: r["score"], reverse=True)[:topK]


def _full_text_fallback_chats(session, domain: str, search_terms: list, topK: int) -> list:
    """Fallback full-text search for UserChat using simple CONTAINS matching."""
    cypher = """
    MATCH (node:UserChat)
    WHERE (node.domain = $domain OR node.domain STARTS WITH ($domain + '.'))
    RETURN node { .id, .raw_text, .domain, .created_by, .created_at } AS chat,
           'user_chat' AS source_type
    LIMIT $limit
    """

    results = []
    for record in session.run(cypher, domain=domain, limit=topK * 5):
        chat_text = record["chat"]["raw_text"].lower() if record["chat"] else ""
        matches = sum(1 for term in search_terms if term.lower() in chat_text)
        if matches > 0:
            results.append({
                "score": matches / len(search_terms),
                "chat": record["chat"],
                "source_type": record["source_type"],
            })

    return sorted(results, key=lambda r: r["score"], reverse=True)[:topK]


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
