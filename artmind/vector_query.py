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
    cypher = """
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
           node {
             .id,
             .name,
             .doc_id,
             .text
           } AS chunk,
           document {
             .id,
             .name,
             .path,
             .domain
           } AS document
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
        rows = [strip_embeddings(serialize_record(record)) for record in session.run(cypher, **params)]
    return {
        "domain": domain,
        "query_type": "vector",
        "question": question,
        "parameters": {"topK": int(topK)},
        "rows": rows,
    }
