# artmind/summary_store.py
"""The one write of a document's summaries (spec 2026-11-01 §1.4).

Retires the document's current `:DocSummary` nodes to `:DocSummaryHistory`, then
MERGEs the staged rows under whichever label each id carries (ids are
deterministic: `<doc_id>:summary`, `<chunk_id>:summary`). An unchanged `key`
keeps the node's vector; a changed one drops it for the sweep to fill. Each
node is linked `SUMMARIZES` to its Document or DocChunk. Runs in the caller's
transaction.
"""
from __future__ import annotations

RETIRE = """
MATCH (s:DocSummary {doc_id: $doc_id})
REMOVE s:DocSummary SET s:DocSummaryHistory
"""

_MERGE = """
UNWIND $rows AS row
OPTIONAL MATCH (o1:DocSummary {{id: row.id}})
OPTIONAL MATCH (o2:DocSummaryHistory {{id: row.id}})
WITH row, coalesce(o1, o2) AS existing
FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
    CREATE (n:DocSummary) SET n = row.props)
WITH row, existing, existing.key AS old_key, existing.embedding AS old_embedding,
     existing._embed_key AS old_embed_key
FOREACH (_ IN CASE WHEN existing IS NULL THEN [] ELSE [1] END |
    REMOVE existing:DocSummaryHistory SET existing:DocSummary SET existing = row.props
    SET existing.embedding = CASE WHEN old_key = row.props.key THEN old_embedding END,
        existing._embed_key = CASE WHEN old_key = row.props.key THEN old_embed_key END)
WITH row
MATCH (n:DocSummary {{id: row.id}})
MATCH (t:{label} {{id: row.target}})
MERGE (n)-[:SUMMARIZES]->(t)
"""


def summary_rows(data: dict | None, document: dict) -> list[dict]:
    """Write rows for `summaries.json` data: the document summary first, then the chunks."""
    if not isinstance(data, dict) or not isinstance(data.get("chunks"), list):
        return []
    common = {"doc_id": document["id"], "_domain": document.get("_domain")}
    for field in ("valid_from", "valid_to"):
        if document.get(field) is not None:
            common[field] = document[field]
    rows: list[dict] = []
    doc = data.get("document")
    if isinstance(doc, dict) and doc.get("summary"):
        sid = f"{document['id']}:summary"
        rows.append({"id": sid, "target": document["id"], "target_label": "Document",
                     "props": {"id": sid, "level": "document", **common,
                               "text": doc["summary"], "key": doc.get("key")}})
    for entry in data["chunks"]:
        if not isinstance(entry, dict) or not entry.get("summary") or not entry.get("chunk_id"):
            continue
        sid = f"{entry['chunk_id']}:summary"
        rows.append({"id": sid, "target": entry["chunk_id"], "target_label": "DocChunk",
                     "props": {"id": sid, "level": "chunk", "chunk_id": entry["chunk_id"], **common,
                               "text": entry["summary"], "key": entry.get("key")}})
    return rows


def write_document_summaries(tx, doc_id: str, rows: list[dict]) -> int:
    tx.run(RETIRE, doc_id=doc_id)
    for label in ("Document", "DocChunk"):
        batch = [r for r in rows if r["target_label"] == label]
        if batch:
            tx.run(_MERGE.format(label=label), rows=batch)
    return len(rows)
