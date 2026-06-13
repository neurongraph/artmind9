from artmind.db import _init_db
from artmind.graph_query import neo4j_session
from utils.functions import load_env


def _setup_neo4j(session, embedding_dim: int) -> None:
    # ── Uniqueness constraints ────────────────────────────────────────────────
    session.run(
        "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (n:DocChunk) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT user_chat_id IF NOT EXISTS FOR (n:UserChat) REQUIRE n.id IS UNIQUE"
    )

    # ── Composite index for exact 3-field entity upserts ──────────────────────
    session.run(
        "CREATE INDEX entity_lookup IF NOT EXISTS FOR (n:Entity) ON (n.name, n.entity_class, n.domain)"
    )

    # ── Single-property indexes for domain filtering (used by nearly every query) ─
    session.run(
        "CREATE INDEX entity_domain IF NOT EXISTS FOR (n:Entity) ON (n.domain)"
    )
    session.run(
        "CREATE INDEX document_domain IF NOT EXISTS FOR (n:Document) ON (n.domain)"
    )
    session.run(
        "CREATE INDEX chunk_domain IF NOT EXISTS FOR (n:DocChunk) ON (n.domain)"
    )
    session.run(
        "CREATE INDEX user_chat_domain IF NOT EXISTS FOR (n:UserChat) ON (n.domain)"
    )

    # ── 2-field composite for name+domain entity lookups (ingest/update writes) ─
    session.run(
        "CREATE INDEX entity_name_domain IF NOT EXISTS FOR (n:Entity) ON (n.name, n.domain)"
    )

    # ── DocChunk.doc_id for chunk-to-document joins and deletes ───────────────
    session.run(
        "CREATE INDEX chunk_doc_id IF NOT EXISTS FOR (n:DocChunk) ON (n.doc_id)"
    )

    # ── Vector indexes ────────────────────────────────────────────────────────
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
    session.run(
        f"CREATE VECTOR INDEX entity_embedding IF NOT EXISTS "
        f"FOR (e:Entity) ON (e.embedding) "
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {embedding_dim}, "
        f"`vector.similarity_function`: 'cosine'}}}}"
    )

    # ── Fulltext indexes ──────────────────────────────────────────────────────
    try:
        session.run(
            "CREATE FULLTEXT INDEX chunk_text_ft IF NOT EXISTS FOR (c:DocChunk) ON EACH [c.text]"
        )
        session.run(
            "CREATE FULLTEXT INDEX user_chat_text_ft IF NOT EXISTS FOR (c:UserChat) ON EACH [c.raw_text]"
        )
        session.run(
            "CREATE FULLTEXT INDEX entity_name_ft IF NOT EXISTS FOR (e:Entity) ON EACH [e.name, e.description]"
        )
    except Exception:
        pass


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
        "neo4j_indexes": [
            "entity_lookup",
            "entity_domain",
            "entity_name_domain",
            "document_domain",
            "chunk_domain",
            "chunk_doc_id",
            "user_chat_domain",
        ],
        "neo4j_vector_indexes": [
            f"chunk_embedding (dim={embedding_dim})",
            f"user_chat_embedding (dim={embedding_dim})",
            f"entity_embedding (dim={embedding_dim})",
        ],
        "neo4j_fulltext_indexes": ["chunk_text_ft", "user_chat_text_ft", "entity_name_ft"],
    }
