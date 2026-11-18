# artmind/passage_store.py
"""The one write of a chunk's passages (spec 2026-10-31 §3.1, §3.3).

Retires the chunk's current passages to :DocPassageHistory, then MERGEs the new
rows under whichever label each id carries (`ingest._merge_relabeled`'s rule:
passage ids are deterministic, so a stable passage reuses its node) and leaves
them under :DocPassage holding exactly `row.props` -- no `embedding`: the sweep
fills it from the embedding cache or the model. Runs in the caller's transaction.

Record time (spec 2026-11-15 §6.1): a new passage gets `_recorded_at`; a reused one
keeps its first (and its `_revived_at`), and one revived from :DocPassageHistory gains `_revived_at` -- unless this
same commit retired it (`_retired_at = $recorded_at`): that is a re-write; a retired
one gets `_retired_at` / `_retired_by`.
"""
from __future__ import annotations

RETIRE_CHUNK_PASSAGES = """
MATCH (p:DocPassage {chunk_id: $chunk_id})
REMOVE p:DocPassage SET p:DocPassageHistory, p._retired_at = $now, p._retired_by = $by
"""

MERGE_PASSAGES = """
UNWIND $rows AS row
OPTIONAL MATCH (o1:DocPassage {id: row.id})
OPTIONAL MATCH (o2:DocPassageHistory {id: row.id})
WITH row, coalesce(o1, o2) AS existing,
     o1 IS NULL AND o2 IS NOT NULL AND o2._retired_at <> $recorded_at AND NOT $keep_retired AS revived
WITH row, existing, revived, existing._recorded_at AS old_recorded, existing._revived_at AS old_revived,
     existing._retired_at AS old_retired_at, existing._retired_by AS old_retired_by
FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
    CREATE (n:DocPassage) SET n = row.props SET n._recorded_at = $recorded_at)
FOREACH (_ IN CASE WHEN existing IS NULL THEN [] ELSE [1] END |
    REMOVE existing:DocPassageHistory SET existing:DocPassage SET existing = row.props
    SET existing._recorded_at = coalesce(old_recorded, $recorded_at),
        existing._revived_at = CASE WHEN revived THEN $recorded_at ELSE old_revived END,
        existing._retired_at = CASE WHEN $keep_retired THEN old_retired_at END,
        existing._retired_by = CASE WHEN $keep_retired THEN old_retired_by END)
WITH row
MATCH (n:DocPassage {id: row.id})
MATCH (c:DocChunk {id: row.chunk_id})
MERGE (n)-[:PART_OF]->(c)
"""


def write_chunk_passages(
    tx, chunk_id: str, rows: list[dict], *, recorded_at: str, retired_by: str, keep_retired: bool = False,
) -> int:
    """`recorded_at` (UTC) is both the record time of new passages and the
    `_retired_at` of retired ones; `retired_by` names why they were retired
    (`version:<n>` from a commit, `resplit` from the passage sweep).
    `keep_retired`: as `ingest._merge_relabeled` (a retired document's commit)."""
    tx.run(RETIRE_CHUNK_PASSAGES, chunk_id=chunk_id, now=recorded_at, by=retired_by)
    if rows:
        tx.run(MERGE_PASSAGES, rows=rows, recorded_at=recorded_at, keep_retired=keep_retired)
    return len(rows)
