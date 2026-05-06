from artmind.db import _init_db
from artmind.graph_query import neo4j_session
from utils.functions import load_env


def _setup_neo4j(session, embedding_dim: int) -> None:
    session.run(
        "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:DocChunk) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT user_chat_id IF NOT EXISTS FOR (n:UserChat) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE INDEX entity_lookup IF NOT EXISTS FOR (n:Entity) ON (n.name, n.entity_class, n.domain)"
    )
    session.run(
        f"CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS "
        f"FOR (c:DocChunk) ON (c.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {embedding_dim}, "
        f"`vector.similarity_function`: 'cosine'}}}}"
    )
    session.run(
        f"CREATE VECTOR INDEX user_chat_embedding IF NOT EXISTS "
        f"FOR (c:UserChat) ON (c.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {embedding_dim}, "
        f"`vector.similarity_function`: 'cosine'}}}}"
    )


def setup_all() -> dict:
    """Initialize SQLite tables and Neo4j constraints/indexes.

    Safe to call at any time — all operations are idempotent (IF NOT EXISTS).
    Returns a summary of what was set up.
    """
    env = load_env()
    embedding_dim = int(env.get("ARTMIND_KG_EMBEDDING_DIMENSIONS", "768"))

    _init_db()

    with neo4j_session() as session:
        _setup_neo4j(session, embedding_dim)

    return {
        "sqlite": "ok",
        "neo4j_constraints": ["document_id", "chunk_id", "user_chat_id"],
        "neo4j_indexes": ["entity_lookup"],
        "neo4j_vector_indexes": [
            f"chunk_embedding (dim={embedding_dim})",
            f"user_chat_embedding (dim={embedding_dim})",
        ],
    }
