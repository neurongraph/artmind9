"""The passage sweep (spec 2026-10-31 §3.2): the permanent reconciliation of
`:DocPassage` with the graph's chunks, and of every stored text vector with the
current embedding key.

1. Build passages for every `:DocChunk` with none, or cut under another
   `split_key()` (a passage-size or rules change rolls out through here).
2. Embed passages with no vector or a stale `_embed_key` (model / prefix change),
   from the machine-local cache first (`embedding_cache`), else the model.
3. Unscoped or domain-scoped runs: re-embed `:UserChat` nodes with a stale key.

Resumable by construction, like the chunk sweep it replaces: every vector is
written as soon as it exists, and a run's own failures are excluded from its
later pages (`skip_ids`) so a dead embedding service cannot loop it.
"""
from __future__ import annotations

from loguru import logger

from artmind import passage_store, passages
from artmind.embedding_cache import EmbeddingCache, cached_embed
from artmind.extraction import embed_document, embed_key as _embed_key
from utils.functions import load_env


def _model() -> str:
    return load_env().get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")


_CHUNKS_NEEDING_PASSAGES = """
MATCH (c:DocChunk)
WHERE trim(coalesce(c.text, '')) <> ''
  AND (NOT EXISTS {{ MATCH (:DocPassage)-[:PART_OF]->(c) }}
       OR EXISTS {{ MATCH (p:DocPassage)-[:PART_OF]->(c) WHERE p._split_key <> $split_key }})
  AND NOT c.id IN $skip_ids{scope}
OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
RETURN c.id AS id, c.doc_id AS doc_id, c._domain AS _domain, c.text AS chunk_text,
       c.header_path AS header_path, c.valid_from AS valid_from, c.valid_to AS valid_to,
       coalesce(d.title, d.name) AS title
LIMIT $batch_size
"""

_PASSAGES_NEEDING_VECTORS = """
MATCH (p:DocPassage)
WHERE (p.embedding IS NULL OR p._embed_key IS NULL OR p._embed_key <> $embed_key)
  AND NOT p.id IN $skip_ids{scope}
RETURN p.id AS id, p.text AS text, p.context AS context
LIMIT $batch_size
"""

_CHATS_NEEDING_VECTORS = """
MATCH (c:UserChat)
WHERE (c.embedding IS NULL OR c._embed_key IS NULL OR c._embed_key <> $embed_key)
  AND NOT c.id IN $skip_ids{scope}
RETURN c.id AS id, c.raw_text AS raw_text
LIMIT $batch_size
"""


def count_passage_gaps(tx) -> int:
    """Passages still missing a current vector (the CLI's heads-up, projection status)."""
    return tx.run(
        "MATCH (p:DocPassage) WHERE p.embedding IS NULL OR p._embed_key IS NULL "
        "OR p._embed_key <> $embed_key RETURN count(p) AS n",
        embed_key=_embed_key(_model()),
    ).single()["n"]


def _scope(var: str, id_field: str, chunk_ids, domain, doc_ids) -> tuple[str, dict]:
    clauses, params = [], {}
    if chunk_ids is not None:
        clauses.append(f"{var}.{id_field} IN $chunk_ids")
        params["chunk_ids"] = list(chunk_ids)
    if domain is not None:
        clauses.append(f"{var}._domain = $domain")
        params["domain"] = domain
    if doc_ids is not None:
        clauses.append(f"{var}.doc_id IN $doc_ids")
        params["doc_ids"] = list(doc_ids)
    return "".join(f"\n  AND {c}" for c in clauses), params


def reconcile_passages(
    session,
    *,
    chunk_ids: list | None = None,
    domain: str | None = None,
    doc_ids: list | None = None,
    embed=None,
    embed_key: str | None = None,
    cache: EmbeddingCache | None | bool = True,
    batch_size: int = 100,
    progress_every: int = 50,
    on_progress=None,
) -> dict:
    """See the module docstring. `chunk_ids=[]` / `doc_ids=[]` are scoped to
    nothing and return at once. `cache=True` opens the default cache; `None`
    disables it (tests). Returns {chunks_split, embedded, remaining, chats_embedded}."""
    result = {"chunks_split": 0, "embedded": 0, "remaining": 0, "chats_embedded": 0}
    if (chunk_ids is not None and not chunk_ids) or (doc_ids is not None and not doc_ids):
        return result
    model = _model()
    embed_key = embed_key or _embed_key(model)
    embed = embed or (lambda text: embed_document(model, text))
    if cache is True:
        cache = EmbeddingCache()

    # 1. passages for chunks that have none, or were cut under another split key
    scope, params = _scope("c", "id", chunk_ids, domain, doc_ids)
    split_key = passages.split_key()
    skipped: set[str] = set()
    while True:
        rows = session.run(_CHUNKS_NEEDING_PASSAGES.format(scope=scope), split_key=split_key,
                           skip_ids=list(skipped), batch_size=batch_size, **params).data()
        if not rows:
            break
        for row in rows:
            chunk = {"id": row["id"], "doc_id": row.get("doc_id"), "_domain": row.get("_domain"),
                     "text": row.get("chunk_text") or "", "header_path": row.get("header_path"),
                     "valid_from": row.get("valid_from"), "valid_to": row.get("valid_to")}
            built = passages.passage_rows(chunk, row.get("title"))
            if not built:          # nothing to split (cannot happen past the trim filter)
                skipped.add(row["id"])
                continue
            passage_store.write_chunk_passages(session, row["id"], built)
            result["chunks_split"] += 1
        if len(rows) < batch_size:
            break

    # 2. vectors for passages with none or a stale key
    scope, params = _scope("p", "chunk_id", chunk_ids, domain, doc_ids)
    failed: set[str] = set()
    while True:
        rows = session.run(_PASSAGES_NEEDING_VECTORS.format(scope=scope), embed_key=embed_key,
                           skip_ids=list(failed), batch_size=batch_size, **params).data()
        if not rows:
            break
        for row in rows:
            text = passages.embedding_text(row.get("text") or "", row.get("context"))
            try:
                vector = cached_embed(cache, embed_key, text, embed)
            except Exception as exc:
                failed.add(row["id"])
                logger.warning("Passage embedding failed for {!r}: {}", row["id"], exc)
                continue
            if not vector:
                failed.add(row["id"])
                continue
            session.run("MATCH (p:DocPassage {id: $id}) SET p.embedding = $embedding, p._embed_key = $embed_key",
                        id=row["id"], embedding=vector, embed_key=embed_key)
            result["embedded"] += 1
            if result["embedded"] % progress_every == 0:
                logger.info("Passage sweep: {} passage(s) embedded so far…", result["embedded"])
                if on_progress:
                    on_progress(result["embedded"], None)
        if len(rows) < batch_size:
            break
    result["remaining"] = len(failed)

    # 3. chats (their own vectors; no passages) on unscoped or domain-scoped runs only
    if chunk_ids is None and doc_ids is None:
        scope = "\n  AND c._domain = $domain" if domain is not None else ""
        extra = {"domain": domain} if domain is not None else {}
        chat_failed: set[str] = set()
        while True:
            rows = session.run(_CHATS_NEEDING_VECTORS.format(scope=scope), embed_key=embed_key,
                               skip_ids=list(chat_failed), batch_size=batch_size, **extra).data()
            if not rows:
                break
            for row in rows:
                try:
                    vector = cached_embed(cache, embed_key, row.get("raw_text") or "", embed)
                except Exception as exc:
                    chat_failed.add(row["id"])
                    logger.warning("Chat embedding failed for {!r}: {}", row["id"], exc)
                    continue
                session.run("MATCH (c:UserChat {id: $id}) SET c.embedding = $embedding, c._embed_key = $embed_key",
                            id=row["id"], embedding=vector, embed_key=embed_key)
                result["chats_embedded"] += 1
            if len(rows) < batch_size:
                break

    if any(result.values()):
        logger.info("Passage sweep: {} chunk(s) split, {} passage(s) embedded, {} skipped, {} chat(s) embedded",
                    result["chunks_split"], result["embedded"], result["remaining"], result["chats_embedded"])
    return result
