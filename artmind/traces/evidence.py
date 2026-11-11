"""Row counts, truncation and evidence ids from a query command's JSON output
(spec 2026-10-11 §4, §5.3). Pure: no I/O, never raises on odd shapes.

Every `artmind query` payload is a dict whose main list is `rows` (exceptions:
`resolve-key` -> `candidates`, hierarchy discovery -> `edge_candidates`,
`views list` -> `views`). Evidence is found by shape, not by command:
a chunk is a dict with `doc_id` plus `chunk_id`, or plus `id` and `name` (and
no `key`, which Observation nodes carry); a
document is a dict with `id` and `path`/`source_path` but no `doc_id`; an
entity is any `_id` string.
"""

from typing import Any

CAP = 200
_ROW_KEYS = ("rows", "candidates", "edge_candidates", "views", "findings", "documents", "pages")
_NOT_AN_ANSWER = ("needs_disambiguation", "no_match")
_WARNINGS_KEPT = 10
_WARNING_CHARS = 300


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
    if isinstance(payload.get("finding"), dict):  # `query insights show`: one finding is an answer
        return 1
    if isinstance(payload.get("page"), dict):  # `query topics show`
        return 1
    return 0


class _Acc:
    def __init__(self) -> None:
        self.entities: dict[str, None] = {}
        self.chunks: dict[tuple, None] = {}
        self.documents: dict[tuple, None] = {}
        self.passages: dict[str, None] = {}
        self.topics: dict[str, None] = {}

    def add(self, bucket: dict, key: Any) -> None:
        if len(bucket) < CAP:
            bucket.setdefault(key, None)


class _ProvenanceAcc:
    """Location-only provenance, grouped in payload encounter order.

    Trace evidence deliberately records identifiers and locations, never the
    source text a query returned.  ``documents`` is keyed by a stable document
    id when there is one, otherwise by its source path; this keeps table-backed
    sources representable too.
    """

    def __init__(self) -> None:
        self.documents: dict[tuple, dict] = {}
        self.tables: dict[tuple, dict] = {}
        self.orphan_chunks: dict[tuple, dict] = {}

    @staticmethod
    def _document(node: Any) -> dict | None:
        """A document ref. Full-shape nodes carry a path; ``--shape brief``
        rows (`doc` / survey rows) carry only id, title and domain, so a ref
        with an id and no path is kept too: the renderer resolves its path."""
        if not isinstance(node, dict):
            return None
        path = node.get("path") or node.get("source_path")
        doc_id = node.get("id") if isinstance(node.get("id"), str) else node.get("doc_id")
        if not isinstance(path, str) and not isinstance(doc_id, str):
            return None
        document = {
            "doc_id": doc_id if isinstance(doc_id, str) else None,
            "path": node.get("path") if isinstance(node.get("path"), str) else None,
            "source_path": node.get("source_path") if isinstance(node.get("source_path"), str) else None,
            "domain": next((node[k] for k in ("_domain", "domain") if isinstance(node.get(k), str)), None),
            "chunks": [],
        }
        if isinstance(node.get("title"), str) and node["title"]:
            document["title"] = node["title"]
        return document

    def add_document(self, node: Any) -> dict | None:
        document = self._document(node)
        if document is None:
            return None
        key = (document["doc_id"], document["source_path"] or document["path"])
        existing = self.documents.get(key)
        if existing is None:
            if len(self.documents) >= CAP:
                return None
            self.documents[key] = document
            existing = document
        else:
            for field in ("path", "source_path", "domain", "title"):
                if existing.get(field) is None and document.get(field) is not None:
                    existing[field] = document[field]
        return existing

    def add_chunk(self, node: Any, document: dict | None, passage: Any = None) -> None:
        if not isinstance(node, dict):
            return
        chunk_id = node.get("chunk_id")
        if not isinstance(chunk_id, str):
            chunk_id = node.get("id")
        doc_id = node.get("doc_id") or (document or {}).get("doc_id")
        if not isinstance(chunk_id, str) or not isinstance(doc_id, str) or chunk_id == doc_id:
            return
        try:
            sequence = int(chunk_id.rsplit("_", 1)[1])
        except (IndexError, ValueError):
            sequence = None
        item = {
            "chunk_id": chunk_id,
            "sequence": sequence,
            "name": node.get("name") if isinstance(node.get("name"), str) else None,
            "header_path": node.get("header_path") if isinstance(node.get("header_path"), str) else None,
            "passages": [],
        }
        if isinstance(passage, dict) and isinstance(passage.get("char_start"), int) and isinstance(passage.get("char_end"), int):
            item["passages"].append({"char_start": passage["char_start"], "char_end": passage["char_end"]})
        target = document
        if target is None:
            key = (chunk_id, doc_id)
            existing = self.orphan_chunks.get(key)
            if existing is None and len(self.orphan_chunks) < CAP:
                self.orphan_chunks[key] = {"doc_id": doc_id, "chunks": [item]}
            elif existing is not None:
                _merge_chunk(existing["chunks"], item)
            return
        _merge_chunk(target["chunks"], item)

    def add_table(self, table: Any) -> None:
        if not isinstance(table, dict) or not isinstance(table.get("source_file"), str):
            return
        key = (table["source_file"], table.get("domain"))
        if key not in self.tables and len(self.tables) < CAP:
            self.tables[key] = {"source_file": table["source_file"], "domain": table.get("domain")}

    def value(self) -> dict | None:
        documents = list(self.documents.values())
        # A few legacy shapes return chunk refs and document refs in sibling
        # lists. Join those once all documents have been encountered.
        by_id = {d.get("doc_id"): d for d in documents if d.get("doc_id")}
        for orphan in self.orphan_chunks.values():
            target = by_id.get(orphan["doc_id"])
            if target is not None:
                for chunk in orphan["chunks"]:
                    _merge_chunk(target["chunks"], chunk)
        out = {"documents": documents, "tables": list(self.tables.values())}
        return out if documents or out["tables"] else None


def _merge_chunk(chunks: list[dict], item: dict) -> None:
    """Deduplicate a chunk while preserving the first retrieval order."""
    existing = next((chunk for chunk in chunks if chunk["chunk_id"] == item["chunk_id"]), None)
    if existing is None:
        if len(chunks) < CAP:
            chunks.append(item)
        return
    for field in ("sequence", "name", "header_path"):
        if existing.get(field) is None and item.get(field) is not None:
            existing[field] = item[field]
    for passage in item.get("passages") or []:
        if passage not in existing["passages"]:
            existing["passages"].append(passage)


def _survey_ids(payload: dict, acc: _Acc) -> None:
    """`query survey`: documents[].doc_id, passages[].id / chunk_id; topics[].slug."""
    for doc in payload.get("documents") or []:
        if not isinstance(doc, dict):
            continue
        if isinstance(doc.get("doc_id"), str):
            acc.add(acc.documents, (doc["doc_id"], doc.get("path") if isinstance(doc.get("path"), str) else None))
        for passage in doc.get("passages") or []:
            if isinstance(passage, dict):
                if isinstance(passage.get("id"), str):
                    acc.add(acc.passages, passage["id"])
                if isinstance(passage.get("chunk_id"), str) and isinstance(doc.get("doc_id"), str):
                    acc.add(acc.chunks, (passage["chunk_id"], doc["doc_id"]))
    for topic in payload.get("topics") or []:
        slug = topic.get("slug") if isinstance(topic, dict) else None
        if isinstance(slug, str):
            acc.add(acc.topics, slug)


def _topic_ids(payload: dict, acc: _Acc) -> None:
    """`query topics list` (pages[].slug) and `topics show` (page.slug)."""
    pages = list(payload.get("pages") or [])
    if isinstance(payload.get("page"), dict):
        pages.append(payload["page"])
    for page in pages:
        slug = page.get("slug") if isinstance(page, dict) else None
        if isinstance(slug, str):
            acc.add(acc.topics, slug)


def _walk(node: Any, acc: _Acc) -> None:
    if isinstance(node, dict):
        if isinstance(node.get("_id"), str):
            acc.add(acc.entities, node["_id"])
        doc_id = node.get("doc_id")
        if isinstance(doc_id, str):
            chunk_id = node.get("chunk_id")
            if chunk_id is None and "name" in node and "key" not in node:
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


def _plan_evidence(payload: dict) -> dict:
    """A `query plan` payload: only `rows` and `executed.result` are evidence; the
    entity, domain, view-match and store candidates are what the plan considered."""
    executed = payload.get("executed") if isinstance(payload.get("executed"), dict) else {}
    result = executed.get("result") if isinstance(executed.get("result"), dict) else {}
    view_run = isinstance(result.get("view"), str) and "version" in result
    return _evidence({**(result if view_run else {}), "rows": payload.get("rows") or []})


def _evidence(payload: dict) -> dict:
    if "plan_version" in payload:
        return _plan_evidence(payload)
    acc = _Acc()
    _walk(payload, acc)
    if payload.get("query_type") == "survey":
        _survey_ids(payload, acc)
    _topic_ids(payload, acc)
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
    if acc.passages:
        ev["passages"] = list(acc.passages)
    if acc.topics:
        ev["topics"] = list(acc.topics)
    if acc.entities:
        ev["entities"] = list(acc.entities)
    if payload.get("status") in _NOT_AN_ANSWER:
        ev["status"] = payload["status"]
    warnings = [w[:_WARNING_CHARS] for w in payload.get("warnings") or [] if isinstance(w, str)]
    if warnings:
        ev["warnings"] = warnings[:_WARNINGS_KEPT]
    guide = payload.get("guide")
    if isinstance(guide, list) and guide:
        ev["guide"] = [g for g in guide if isinstance(g, str)][:2]
    nxt = payload.get("next")
    if isinstance(nxt, str) and nxt.strip():
        ev["next"] = nxt.split("; ")[0].strip()[:_WARNING_CHARS]
    provenance = _provenance(payload)
    if provenance is not None:
        ev["provenance"] = provenance
    return ev


def _provenance(payload: dict) -> dict | None:
    """Extract location-only citation material from known query result shapes.

    The generic evidence walker is intentionally broad for review diagnostics;
    provenance is narrower so entity ids and arbitrary nested dicts cannot turn
    into user-facing citations.
    """
    acc = _ProvenanceAcc()
    # `--shape brief` rows: `doc {id,title,domain,..}` plus a bare `chunk_id`
    # (vector-text, survey --expand, chunks); survey's `documents[]` rows carry
    # `doc_id` and the best chunk under `best`. None of them holds a path.
    for row in [*(payload.get("rows") or []), *(payload.get("passages") or [])]:
        if isinstance(row, dict) and isinstance(row.get("doc"), dict) and not isinstance(row.get("document"), dict):
            document = acc.add_document(row["doc"])
            chunk = row.get("chunk") if isinstance(row.get("chunk"), dict) else {"chunk_id": row.get("chunk_id")}
            acc.add_chunk(chunk, document, row.get("passage"))
    if payload.get("query_type") == "survey":
        for item in payload.get("documents") or []:
            if not isinstance(item, dict) or not isinstance(item.get("doc_id"), str):
                continue
            best = item.get("best") if isinstance(item.get("best"), dict) else {}
            passage = item.get("passage") if isinstance(item.get("passage"), dict) else {}
            chunk_id = passage.get("chunk_id") or best.get("chunk_id")
            full_passages = [p for p in item.get("passages") or [] if isinstance(p, dict) and isinstance(p.get("chunk_id"), str)]
            # A brief "line" row is a title only: the agent saw no evidence
            # for it, so it is not cited.
            if not isinstance(chunk_id, str) and not full_passages:
                continue
            document = acc.add_document(item)
            if isinstance(chunk_id, str):
                acc.add_chunk({"chunk_id": chunk_id, "doc_id": item["doc_id"]}, document, passage)
            for p in full_passages:
                acc.add_chunk({"chunk_id": p["chunk_id"], "doc_id": item["doc_id"]}, document)
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        document = acc.add_document(row.get("document"))
        chunk = row.get("chunk")
        acc.add_chunk(chunk, document, row.get("passage"))
        for item in row.get("chunks") or []:
            if isinstance(item, dict):
                embedded = acc.add_document(item.get("document"))
                acc.add_chunk(item, embedded or document)
        for item in row.get("doc_sources") or []:
            acc.add_chunk(item, None)
        for item in row.get("source_documents") or []:
            acc.add_document(item)
        for item in row.get("evidence") or []:
            if not isinstance(item, dict):
                continue
            evidence_document = acc.add_document(item.get("document"))
            acc.add_chunk({"chunk_id": item.get("chunk_id"), "doc_id": item.get("doc_id")}, evidence_document)
    # Pattern rows have source_documents/doc_sources inside each row; once the
    # complete payload has been read, orphan chunks are joined by doc id.
    for table in payload.get("source_tables") or []:
        acc.add_table(table)
    return acc.value()
