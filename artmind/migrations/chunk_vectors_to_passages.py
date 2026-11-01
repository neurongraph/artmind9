"""One-off cleanup: chunk vectors -> passages (spec 2026-10-31 §3.6).

REMOVE THIS MODULE, its call in `setup.setup_all` and
`test/test_migration_chunk_vectors.py` once every graph in use prints
`legacy chunk vectors: none` from `artmind setup` (tracked in the GitHub issue
filed when this landed). A fresh graph never needs it: setup no longer creates
the legacy indexes.

Drops `chunk_embedding` and `chunk_text_ft` and removes `embedding` from
`:DocChunk`/`:DocChunkHistory` -- but only once every non-blank `:DocChunk` has
passages, so vector-text never loses its index before passages exist.
"""
from __future__ import annotations

LEGACY_INDEXES = ("chunk_embedding", "chunk_text_ft")
BATCH = 1000
NONE = "legacy chunk vectors: none"


def run(session) -> dict:
    present = [r["name"] for r in session.run(
        "SHOW INDEXES YIELD name WHERE name IN $names RETURN name", names=list(LEGACY_INDEXES)).data()]
    present = [n for n in LEGACY_INDEXES if n in present]
    leftover = session.run(
        "MATCH (c) WHERE (c:DocChunk OR c:DocChunkHistory) AND c.embedding IS NOT NULL RETURN count(c) AS n"
    ).single()["n"]
    if not present and not leftover:
        return {"status": "none", "report": NONE}

    lacking = session.run(
        """
        MATCH (c:DocChunk)
        WHERE trim(coalesce(c.text, '')) <> ''
          AND NOT EXISTS { MATCH (:DocPassage)-[:PART_OF]->(c) }
        RETURN c._domain AS domain, count(c) AS n
        ORDER BY domain
        """
    ).data()
    if lacking:
        by_domain = {r["domain"]: r["n"] for r in lacking}
        total = sum(by_domain.values())
        return {
            "status": "waiting",
            "domains_without_passages": by_domain,
            "report": (f"legacy chunk vectors: kept — {total} chunk(s) in {', '.join(map(str, by_domain))} "
                       "have no passages yet; run `artmind ingest embed-passages` (or `artmind vault sync`) "
                       "inside the vault, then `artmind setup` again"),
        }

    for name in present:
        session.run(f"DROP INDEX {name} IF EXISTS")
    removed = 0
    while True:
        n = session.run(
            f"""
            MATCH (c) WHERE (c:DocChunk OR c:DocChunkHistory) AND c.embedding IS NOT NULL
            WITH c LIMIT {BATCH}
            REMOVE c.embedding
            RETURN count(c) AS n
            """
        ).single()["n"]
        removed += n
        if n < BATCH:
            break
    return {"status": "cleaned", "dropped_indexes": present, "vectors_removed": removed, "report": NONE}
