import ollama

from artmind.graph_query import neo4j_session, serialize_record, strip_embeddings
from utils.functions import load_env, log_llm_call


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
    WHERE node.domain = $domain
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
    WHERE node.domain = $domain
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
        chat_rows = [
            strip_embeddings(serialize_record(record))
            for record in session.run(cypher_chats, **params)
        ]

    all_rows = sorted(chunk_rows + chat_rows, key=lambda r: r.get("score", 0), reverse=True)[:int(topK)]

    return {
        "domain": domain,
        "query_type": "vector",
        "question": question,
        "parameters": {"topK": int(topK)},
        "rows": all_rows,
    }
