"""Row counts, truncation and evidence ids from a query command's JSON output
(spec 2026-10-11 §4, §5.3). Pure: no I/O, never raises on odd shapes.

Every `artmind query` payload is a dict whose main list is `rows` (exceptions:
`resolve-key` -> `candidates`, hierarchy discovery -> `edge_candidates`,
`views list` -> `views`). Evidence is found by shape, not by command:
a chunk is a dict with `doc_id` plus `chunk_id`, or plus `id` and `name`; a
document is a dict with `id` and `path`/`source_path` but no `doc_id`; an
entity is any `_id` string.
"""

from typing import Any

CAP = 200
_ROW_KEYS = ("rows", "candidates", "edge_candidates", "views")
_NOT_AN_ANSWER = ("needs_disambiguation", "no_match")


def summarize(payload: Any) -> dict:
    if not isinstance(payload, dict):
        return {"rows": len(payload) if isinstance(payload, list) else 0,
                "truncated": False, "rows_total": None, "evidence": {}}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    rows_total = payload.get("rows_total", summary.get("total"))
    return {
        "rows": _count(payload),
        "truncated": bool(payload.get("truncated") or summary.get("truncated")),
        "rows_total": rows_total if isinstance(rows_total, int) else None,
        "evidence": _evidence(payload),
    }


def _count(payload: dict) -> int:
    if payload.get("status") in _NOT_AN_ANSWER:
        return 0
    for key in _ROW_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


class _Acc:
    def __init__(self) -> None:
        self.entities: dict[str, None] = {}
        self.chunks: dict[tuple, None] = {}
        self.documents: dict[tuple, None] = {}

    def add(self, bucket: dict, key: Any) -> None:
        if len(bucket) < CAP:
            bucket.setdefault(key, None)


def _walk(node: Any, acc: _Acc) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("_id"), str):
            acc.add(acc.entities, node["_id"])
        doc_id = node.get("doc_id")
        if isinstance(doc_id, str):
            chunk_id = node.get("chunk_id")
            if chunk_id is None and "name" in node:
                chunk_id = node.get("id")
            if isinstance(chunk_id, str) and chunk_id != doc_id:
                acc.add(acc.chunks, (chunk_id, doc_id))
        else:
            path = node.get("path") or node.get("source_path")
            if isinstance(path, str) and isinstance(node.get("id"), str):
                acc.add(acc.documents, (node["id"], path))
        for value in node.values():
            _walk(value, acc)
    elif isinstance(node, list):
        for value in node:
            _walk(value, acc)


def _resolved(payload: dict) -> list[dict]:
    out = []
    if payload.get("query_type") == "entity_resolve":
        for row in payload.get("rows") or []:
            entity = row.get("entity") if isinstance(row, dict) else None
            if isinstance(entity, dict):
                out.append({"id": entity.get("_id"), "name": entity.get("name"),
                            "entity_class": entity.get("entity_class"), "score": row.get("score")})
    elif isinstance(payload.get("candidates"), list):
        for cand in payload["candidates"]:
            if isinstance(cand, dict):
                out.append({"id": cand.get("_id"), "name": cand.get("name", cand.get("value")),
                            "entity_class": cand.get("entity_class"), "score": cand.get("score")})
    return out[:CAP]


def _evidence(payload: dict) -> dict:
    acc = _Acc()
    _walk(payload, acc)
    for table in payload.get("source_tables") or []:
        if isinstance(table, dict) and isinstance(table.get("source_file"), str):
            acc.add(acc.documents, (None, table["source_file"]))

    ev: dict[str, Any] = {}
    if isinstance(payload.get("view"), str) and "version" in payload:
        ev["view"] = {"name": payload["view"], "version": payload.get("version"),
                      "status": payload.get("status"), "params": payload.get("params")}
    if isinstance(payload.get("generated_cypher"), str):
        ev["cypher"] = payload["generated_cypher"]
    if isinstance(payload.get("generated_sql"), str):
        ev["sql"] = payload["generated_sql"]
    resolved = _resolved(payload)
    if resolved:
        ev["resolved"] = resolved
    if acc.chunks:
        ev["chunks"] = [{"chunk_id": c, "doc_id": d} for c, d in acc.chunks]
    if acc.documents:
        ev["documents"] = [{"doc_id": d, "path": p} for d, p in acc.documents]
    if acc.entities:
        ev["entities"] = list(acc.entities)
    return ev
