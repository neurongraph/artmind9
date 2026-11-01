# artmind/passage_store.py
"""The one write of a chunk's passages (spec 2026-10-31 §3.1, §3.3).

Retires the chunk's current passages to :DocPassageHistory, then MERGEs the new
rows under whichever label each id carries (`ingest._merge_relabeled`'s rule:
passage ids are deterministic, so a stable passage reuses its node) and leaves
them under :DocPassage holding exactly `row.props` -- no `embedding`: the sweep
fills it from the embedding cache or the model. Runs in the caller's transaction.
"""
from __future__ import annotations

RETIRE_CHUNK_PASSAGES = """
MATCH (p:DocPassage {chunk_id: $chunk_id})
REMOVE p:DocPassage SET p:DocPassageHistory
"""

MERGE_PASSAGES = """
UNWIND $rows AS row
OPTIONAL MATCH (o1:DocPassage {id: row.id})
OPTIONAL MATCH (o2:DocPassageHistory {id: row.id})
WITH row, coalesce(o1, o2) AS existing
FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
    CREATE (n:DocPassage) SET n = row.props)
FOREACH (_ IN CASE WHEN existing IS NULL THEN [] ELSE [1] END |
    REMOVE existing:DocPassageHistory SET existing:DocPassage SET existing = row.props)
WITH row
MATCH (n:DocPassage {id: row.id})
MATCH (c:DocChunk {id: row.chunk_id})
MERGE (n)-[:PART_OF]->(c)
"""


def write_chunk_passages(tx, chunk_id: str, rows: list[dict]) -> int:
    tx.run(RETIRE_CHUNK_PASSAGES, chunk_id=chunk_id)
    if rows:
        tx.run(MERGE_PASSAGES, rows=rows)
    return len(rows)
