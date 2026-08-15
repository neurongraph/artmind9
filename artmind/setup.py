import shutil

from artmind.db import _init_db
from artmind.graph_query import neo4j_session
from paths import (
    ARTMIND_DATA_DIR,
    ARTMIND_HOME,
    DOMAIN_SCHEMAS_DIR,
    GRAPH_SNAPSHOT_DIR,
    JOBS_DIR,
    KG_DIR,
    LOGS_DIR,
    MARKDOWNS_DIR,
    ORIGINALS_DIR,
    PACKAGE_ENV_EXAMPLE,
    PACKAGE_OPENCODE_DIR,
    PACKAGE_SCHEMAS_DIR,
    PACKAGE_SKILLS_DIR,
    REFINE_DIR,
    STRUCTURED_DIR,
    STRUCTURED_SNAPSHOT_DIR,
)
from utils.functions import load_env


def _seed_tree(src, dest, *, overwrite: bool = False) -> int:
    """Copy each top-level entry of ``src`` into ``dest``. Returns entries written.

    Two policies, by what the tree holds:

    - ``overwrite=False`` — *user data* (``.env`` only). Existing entries are
      skipped so local edits survive re-running init.
    - ``overwrite=True`` — *package assets* (skills, opencode persona, domain
      schemas). These are shipped alongside the code with the package as their
      source of truth, so a reinstall must replace whatever the run folder
      holds; skipping them would freeze them at whatever version first seeded
      the run folder, and edits made in ``artmind/skills/`` or
      ``artmind/domains/schemas/`` would silently never reach the chat agent
      or the CLI.

    Entries are replaced wholesale, not merged, so a file deleted from a skill
    also disappears from the run folder. Names the package does not ship are left
    alone either way, so a domain added via `artmind domains add` (never
    committed to the package) is never pruned — but renaming or removing a
    schema in the package leaves its old run-folder copy in place too, since
    that name simply no longer appears in `src.iterdir()`.
    """
    if not src.is_dir():
        return 0
    dest.mkdir(parents=True, exist_ok=True)
    written = 0
    for entry in src.iterdir():
        if entry.name == ".DS_Store":
            continue
        target = dest / entry.name
        if target.exists():
            if not overwrite:
                continue
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)
        written += 1
    return written


def scaffold_run_folder() -> dict:
    """Create the run folder + data dirs and seed .env, skills and schemas.

    Pure filesystem work — needs no Neo4j/config, so it is safe to run right
    after install, before the user has filled in ``~/.artmind/.env``.

    Idempotent, but not inert: package assets (skills, opencode, domain schemas)
    are refreshed from the package on every run, while user data (``.env``) is
    only seeded when absent. See ``_seed_tree``.
    """
    run_env = ARTMIND_HOME / ".env"
    skills_dest = ARTMIND_HOME / ".claude" / "skills"
    opencode_dest = ARTMIND_HOME / ".opencode"

    for directory in (
        ARTMIND_HOME,
        skills_dest,
        opencode_dest,
        DOMAIN_SCHEMAS_DIR,
        LOGS_DIR,
        ARTMIND_DATA_DIR,
        ORIGINALS_DIR,
        MARKDOWNS_DIR,
        JOBS_DIR,
        KG_DIR,
        REFINE_DIR,
        GRAPH_SNAPSHOT_DIR,
        STRUCTURED_DIR,
        STRUCTURED_SNAPSHOT_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    seeded_env = False
    if not run_env.exists() and PACKAGE_ENV_EXAMPLE.is_file():
        shutil.copy2(PACKAGE_ENV_EXAMPLE, run_env)
        seeded_env = True

    # Package assets: always refreshed — the package is their source of truth.
    skills_refreshed = _seed_tree(PACKAGE_SKILLS_DIR, skills_dest, overwrite=True)
    opencode_refreshed = _seed_tree(PACKAGE_OPENCODE_DIR, opencode_dest, overwrite=True)
    schemas_copied = _seed_tree(PACKAGE_SCHEMAS_DIR, DOMAIN_SCHEMAS_DIR, overwrite=True)

    return {
        "run_folder": str(ARTMIND_HOME),
        "data_dir": str(ARTMIND_DATA_DIR),
        "env": "seeded from template" if seeded_env else str(run_env),
        "skills_refreshed": skills_refreshed,
        "schemas_copied": schemas_copied,
        "opencode_refreshed": opencode_refreshed,
    }


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
    session.run(
        "CREATE CONSTRAINT conflict_id IF NOT EXISTS FOR (n:Conflict) REQUIRE n.id IS UNIQUE"
    )

    # ── Entity.id: unique constraint, or a plain index as fallback ────────────
    # Exact-id lookup is the query layer's primary retrieval path, so this must
    # be backed by an index either way. Entities are written with CREATE (not
    # MERGE on id), so a pre-existing graph may hold duplicate ids — there the
    # constraint cannot be created and a plain index keeps lookups fast until
    # the duplicates are cleaned up.
    try:
        session.run(
            "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE"
        )
        entity_id_schema = "entity_id (unique)"
    except Exception:
        session.run("CREATE INDEX entity_id_idx IF NOT EXISTS FOR (n:Entity) ON (n.id)")
        entity_id_schema = "entity_id_idx (fallback index; duplicate Entity.id values exist)"

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
    # Path-based logical identity (A1c): the lookup re-ingest uses to reuse a
    # document's physical id and bump its version instead of minting a duplicate.
    session.run(
        "CREATE INDEX document_logical_id IF NOT EXISTS FOR (n:Document) ON (n.logical_id)"
    )
    session.run(
        "CREATE INDEX chunk_domain IF NOT EXISTS FOR (n:DocChunk) ON (n.domain)"
    )
    # Content-addressed block hash (A1a); the signal a later delta classifier (A4)
    # keys on to tell changed blocks from unchanged ones across re-ingest.
    session.run(
        "CREATE INDEX chunk_block_hash IF NOT EXISTS FOR (n:DocChunk) ON (n.block_hash)"
    )
    session.run(
        "CREATE INDEX user_chat_domain IF NOT EXISTS FOR (n:UserChat) ON (n.domain)"
    )

    # ── Temporal range indexes (canonical valid-time / event-time) ────────────
    session.run("CREATE INDEX entity_valid_from IF NOT EXISTS FOR (n:Entity) ON (n.valid_from)")
    session.run("CREATE INDEX entity_valid_to IF NOT EXISTS FOR (n:Entity) ON (n.valid_to)")
    session.run("CREATE INDEX entity_event_at IF NOT EXISTS FOR (n:Entity) ON (n.event_at)")
    session.run("CREATE INDEX chunk_valid_from IF NOT EXISTS FOR (n:DocChunk) ON (n.valid_from)")
    session.run("CREATE INDEX chunk_valid_to IF NOT EXISTS FOR (n:DocChunk) ON (n.valid_to)")
    session.run("CREATE INDEX document_valid_from IF NOT EXISTS FOR (n:Document) ON (n.valid_from)")
    session.run("CREATE INDEX document_valid_to IF NOT EXISTS FOR (n:Document) ON (n.valid_to)")
    session.run("CREATE INDEX conflict_status IF NOT EXISTS FOR (n:Conflict) ON (n.status)")

    # ── History zone (:EntityVersion) ─────────────────────────────────────────
    # Snapshots of overwritten entity property values. Deliberately NOT labelled
    # :Entity and carrying no class label, so no entity query, no vector index,
    # and no refine pass can reach them without asking explicitly.
    session.run(
        "CREATE CONSTRAINT entity_version_id IF NOT EXISTS "
        "FOR (n:EntityVersion) REQUIRE n.id IS UNIQUE"
    )
    session.run(
        "CREATE INDEX entity_version_entity IF NOT EXISTS FOR (n:EntityVersion) ON (n.entity_id)"
    )
    session.run(
        "CREATE INDEX entity_version_valid_to IF NOT EXISTS FOR (n:EntityVersion) ON (n.valid_to)"
    )
    session.run(
        "CREATE INDEX entity_version_domain IF NOT EXISTS FOR (n:EntityVersion) ON (n.domain)"
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

    # ── Structured-store catalogue (Table/TableColumn/EntityClass) ────────────
    # MERGE keys are synthetic composite `key` props (not node-key constraints,
    # which are Enterprise-only). These labels are distinct from :Entity by
    # design — never carry :Entity — so they stay out of query graph
    # pattern*/vector search/metadata.
    session.run(
        "CREATE CONSTRAINT cat_table_key IF NOT EXISTS FOR (n:Table) REQUIRE n.key IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT cat_column_key IF NOT EXISTS FOR (n:TableColumn) REQUIRE n.key IS UNIQUE"
    )
    session.run(
        "CREATE CONSTRAINT cat_entityclass_key IF NOT EXISTS FOR (n:EntityClass) REQUIRE n.key IS UNIQUE"
    )
    session.run(
        "CREATE INDEX cat_table_domain IF NOT EXISTS FOR (n:Table) ON (n.domain)"
    )

    return {"entity_id_schema": entity_id_schema}


def setup_all() -> dict:
    """Initialize SQLite tables and Neo4j constraints/indexes.

    Safe to call at any time — all operations are idempotent (IF NOT EXISTS).
    Returns a summary of what was set up.
    """
    scaffold = scaffold_run_folder()
    env = load_env()
    embedding_dim = int(env.get("ARTMIND_KG_EMBEDDING_DIMENSIONS", "768"))

    _init_db()

    with neo4j_session() as session:
        neo4j_notes = _setup_neo4j(session, embedding_dim)

    return {
        "scaffold": scaffold,
        "sqlite": "ok",
        "neo4j_constraints": [
            "document_id",
            "chunk_id",
            "user_chat_id",
            "conflict_id",
            neo4j_notes["entity_id_schema"],
            "cat_table_key",
            "cat_column_key",
            "cat_entityclass_key",
            "entity_version_id",
        ],
        "neo4j_indexes": [
            "entity_lookup",
            "entity_domain",
            "entity_name_domain",
            "document_domain",
            "document_logical_id",
            "chunk_domain",
            "chunk_block_hash",
            "chunk_doc_id",
            "user_chat_domain",
            "entity_valid_from",
            "entity_valid_to",
            "entity_event_at",
            "chunk_valid_from",
            "chunk_valid_to",
            "document_valid_from",
            "document_valid_to",
            "conflict_status",
            "cat_table_domain",
            "entity_version_entity",
            "entity_version_valid_to",
            "entity_version_domain",
        ],
        "neo4j_vector_indexes": [
            f"chunk_embedding (dim={embedding_dim})",
            f"user_chat_embedding (dim={embedding_dim})",
            f"entity_embedding (dim={embedding_dim})",
        ],
        "neo4j_fulltext_indexes": ["chunk_text_ft", "user_chat_text_ft", "entity_name_ft"],
    }
