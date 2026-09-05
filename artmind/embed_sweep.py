"""Resumable chunk-embed sweep — the `:DocChunk` counterpart to
`embed_missing_entity_embeddings` (artmind/ingest.py).

Restoring from committed staging (docs/vault.md, "Embeddings") writes chunks
with no vector: embeddings are stripped from `data/kg/**/chunks.json` before
it is committed, because they are a pure function of `(text, embedding model)`
and carry no useful git delta. `write-to-graph` writes the chunk with
`embedding: []`/absent, and this sweep fills it back in afterwards, on demand.
"""
from __future__ import annotations

from loguru import logger

from artmind.extraction import embed_text as _embed_text
from utils.functions import load_env


def count_unembedded_chunks(tx) -> int:
    """Count `:DocChunk` nodes still missing a vector.

    Takes anything exposing Neo4j's `.run()` — a `Session` or a `Transaction` both work — so
    this one query serves both the CLI's pre-sweep heads-up (`_run_chunk_embed_sweep`, a
    `Session`) and `projection.status`'s standing gap report (a `Transaction`), rather than
    the same Cypher living in three places.
    """
    return tx.run(
        "MATCH (c:DocChunk) WHERE c.embedding IS NULL RETURN count(c) AS n"
    ).single()["n"]


def embed_missing_chunk_embeddings(
    session,
    *,
    chunk_ids: list | None = None,
    domain: str | None = None,
    embed=None,
    batch_size: int = 100,
    progress_every: int = 50,
    on_progress=None,
) -> dict:
    """Embed every `:DocChunk` whose `.embedding` is still `NULL`.

    Scoped to `chunk_ids` when given — a post-commit sweep embeds only the
    chunks that commit just wrote, not the whole graph. `chunk_ids=None`
    (the default) is unscoped and behaves exactly as before: every
    unembedded `:DocChunk` in the graph is a candidate. `chunk_ids=[]` is
    scoped to nothing and always returns `{"embedded": 0, "remaining": 0}`
    without running a query — distinct from `None`'s unscoped sweep.

    Also scoped to `domain` when given — a batch ingest's deferred rebuild
    sweeps only the domain it just rebuilt, not the whole graph, mirroring
    `embed_missing_entity_embeddings`'s own `keys`-scoping note ("Scoped to
    `keys` when given — an incremental ingest sweeps only what it dirtied,
    not the whole domain."). `domain=None` (the default) omits the clause
    entirely. `chunk_ids` and `domain` may be given together — both scope
    clauses apply, AND'd — though in practice each real caller uses one or
    the other, not both.

    Mirrors `embed_missing_entity_embeddings`'s shape: fetch the chunks that
    still need a vector, embed each one's `.text`, write the vector back
    matched on chunk identity (`c.id` — the same identifier `_load_staged`
    and `_commit_document_tx` key chunks by), and never write a null or
    partial vector — a chunk whose `embed()` call fails is left alone,
    still `NULL`, for the next sweep to pick up.

    **Resumable by construction.** Chunks are fetched and written back
    `batch_size` at a time, and each batch's vectors are committed to Neo4j
    before the next batch is even fetched. So interrupting a run — Ctrl-C, a
    crashed process, a dead embedding service — leaves every chunk the sweep
    finished with a vector, and every chunk it hadn't reached yet still
    matching `embedding IS NULL`, which is exactly what the next run selects.
    There is no partial-batch state to clean up and nothing to roll back.
    That is the whole reason this is a sweep rather than inline work in
    `write-to-graph` (docs/vault.md, "Embeddings").

    `embed` defaults to the real embedding call ingest itself uses for chunk
    text (`artmind.extraction.embed_text`, resolved against the same
    `ARTMIND_KG_EMBEDDINGS_MODEL` env var `embed_entities_backfill` reads) —
    tests inject a stub instead. `on_progress(done, total)` fires every
    `progress_every` chunks so a long run is observable programmatically; the
    same progress is logged through loguru so a real run is visible without
    one.

    Returns ``{"embedded": N, "remaining": M}`` — ``remaining`` counts chunks
    that were fetched but could not be embedded (and so are still `NULL`),
    not a separate query, since a mocked graph session that answers every
    query the same way makes a second read here untrustworthy in tests.

    **Terminates even when the embedding service is completely down.** A
    chunk whose `embed()` call fails is left `NULL` in the graph, which would
    make it eligible for the very next page's `WHERE embedding IS NULL` —
    re-fetching the same stuck chunks forever the moment failures reach
    `batch_size`. So this run's own failures are tracked and excluded from
    every subsequent page (`AND NOT c.id IN $skip_ids`); each page then
    either makes forward progress on chunks it hasn't tried yet or comes back
    short, and the loop provably ends either way. That exclusion is scoped to
    this call only — nothing is written for a failed chunk, so a *later* run
    (a fresh call, e.g. after fixing a dead embedding service) sees it as
    `NULL` again and retries it, same as any other resumed chunk.
    """
    if chunk_ids is not None and len(chunk_ids) == 0:
        # Scoped to nothing — distinct from `None`'s unscoped sweep. Return
        # immediately rather than round-tripping a query that would always
        # come back empty.
        return {"embedded": 0, "remaining": 0}

    if embed is None:
        embed_model = load_env().get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")
        embed = lambda text: _embed_text(embed_model, text)

    embedded, failed, total = 0, 0, 0
    failed_ids: set[str] = set()

    id_scope = ""
    scope_params: dict = {}
    if chunk_ids is not None:
        id_scope = " AND c.id IN $chunk_ids"
        scope_params["chunk_ids"] = list(chunk_ids)

    domain_scope = ""
    if domain is not None:
        domain_scope = " AND c._domain = $domain"
        scope_params["domain"] = domain

    while True:
        rows = session.run(
            f"""
            MATCH (c:DocChunk)
            WHERE c.embedding IS NULL AND NOT c.id IN $skip_ids{id_scope}{domain_scope}
            RETURN c.id AS id, c.text AS text
            LIMIT $batch_size
            """,
            batch_size=batch_size,
            skip_ids=list(failed_ids),
            **scope_params,
        ).data()
        if not rows:
            break
        total += len(rows)

        for row in rows:
            try:
                embedding = embed(row["text"])
            except Exception as e:
                failed += 1
                failed_ids.add(row["id"])
                logger.warning("Chunk embedding failed for {!r}: {}", row["id"], e)
                continue
            # Written immediately, one chunk at a time — this is what makes
            # the sweep resumable: nothing is buffered past the point where
            # it's already safely in the graph.
            session.run(
                "MATCH (c:DocChunk {id: $id}) SET c.embedding = $embedding",
                id=row["id"],
                embedding=embedding,
            )
            embedded += 1
            if embedded % progress_every == 0:
                logger.info("Embed sweep: {} chunk(s) embedded so far…", embedded)
                if on_progress:
                    on_progress(embedded, total)

        if len(rows) < batch_size:
            break

    if embedded or failed:
        logger.info(
            "Embed sweep: {} chunk(s) embedded, {} skipped (still missing a vector)",
            embedded, failed,
        )
    return {"embedded": embedded, "remaining": failed}
