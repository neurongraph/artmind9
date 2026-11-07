"""Bundle adapters (P3 spec §6.2): one command's payload -> bundle rows.

`evidence` rows carry text and a document (the only thing an answer may cite); `documents` rows are
brief document lines; `facts` are graph and SQL rows (brief, linked to chunk ids); `guides` steer
(topic pages, insights) and are never cited. A routes test checks that every command routes.yaml can
emit has an adapter here.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass, field

from artmind.query_run.facts import main_list

PASSAGE_CHARS = 1200
SCALAR_CHARS = 300
CONNECTIONS = 10


@dataclass
class Rows:
    evidence: list = field(default_factory=list)
    documents: list = field(default_factory=list)
    facts: list = field(default_factory=list)
    guides: list = field(default_factory=list)


def _cut(text, n) -> str:
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1] + "…"


def doc_ref(d: dict | None) -> dict:
    """Brief `doc` rows pass through; full `document` rows are reduced to the brief fields."""
    d = d or {}
    title = d.get("title") or (posixpath.splitext(posixpath.basename(str(d["name"])))[0] if d.get("name") else None)
    status = d.get("status") or ("superseded" if d.get("superseded_by") or d.get("superseded") else "current")
    return {"id": d.get("id") or d.get("doc_id"), "title": title, "domain": d.get("domain") or d.get("_domain"),
            "date": d.get("date") or d.get("valid_from"), "status": status}


def _evidence(kind, text, passage_id, chunk_id, relevance, doc) -> dict:
    return {"kind": kind, "text": _cut(text, PASSAGE_CHARS), "passage_id": passage_id, "chunk_id": chunk_id,
            "relevance": relevance, "doc": doc}


def _document_row(d: dict) -> dict:
    return {"doc_id": d.get("doc_id") or d.get("id"), "title": d.get("title"), "domain": d.get("domain"),
            "date": d.get("date"), "status": d.get("status") or "current", "gist": d.get("gist"),
            "gist_source": d.get("gist_source")}


def scalars(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, str):
            out[k] = _cut(v, SCALAR_CHARS)
        elif v is None or isinstance(v, (bool, int, float)):
            out[k] = v
        elif isinstance(v, list):
            out[f"{k}_count"] = len(v)
    return out


def _passage_rows(rows, out: Rows) -> None:
    for r in rows:
        if not isinstance(r, dict) or r.get("chat"):
            continue
        if isinstance(r.get("text"), str):  # brief passage row
            out.evidence.append(_evidence("passage" if r.get("passage_id") else "chunk", r["text"], r.get("passage_id"),
                                          r.get("chunk_id"), r.get("relevance"), doc_ref(r.get("doc"))))
            continue
        chunk = r.get("chunk") if isinstance(r.get("chunk"), dict) else {}
        if isinstance(chunk.get("text"), str):  # full row
            passage = r.get("passage") if isinstance(r.get("passage"), dict) else {}
            doc = doc_ref(r.get("document") or r.get("doc") or {"id": chunk.get("doc_id")})
            out.evidence.append(_evidence("passage" if passage.get("id") else "chunk", chunk["text"], passage.get("id"),
                                          chunk.get("id"), r.get("relevance"), doc))


def _vector_text(p, out):
    _passage_rows(main_list(p), out)


def _survey(p, out):
    for d in p.get("documents") or []:
        if not isinstance(d, dict):
            continue
        out.documents.append(_document_row(d))
        passages = [d["passage"]] if isinstance(d.get("passage"), dict) else [x for x in d.get("passages") or [] if isinstance(x, dict)]
        for x in passages:
            out.evidence.append(_evidence("passage", x.get("text"), x.get("passage_id"), x.get("chunk_id"),
                                          x.get("relevance"), doc_ref(d)))
    for t in p.get("topics") or []:
        if isinstance(t, dict):
            out.guides.append({"kind": "topic_page", "slug": t.get("slug"), "title": t.get("title"), "stale": bool(t.get("stale"))})


def _entity_context(p, out):
    for r in main_list(p):
        if not isinstance(r, dict):
            continue
        entity = r.get("entity") or scalars(r.get("entityData") or {})
        chunks = [c for c in r.get("chunks") or [] if isinstance(c, dict)]
        out.facts.append({"kind": "entity", "row": {"entity": entity,
                                                   "connections": list(r.get("connections") or [])[:CONNECTIONS],
                                                   "connections_total": r.get("connections_total")},
                          "chunk_ids": [c.get("chunk_id") or c.get("id") for c in chunks]})
        _passage_rows(chunks, out)
        for d in r.get("source_documents") or []:
            if isinstance(d, dict):
                out.documents.append(_document_row(d))


def _entity_rows(p, out):
    for r in main_list(p):
        if not isinstance(r, dict):
            continue
        out.facts.append({"kind": "entity", "row": scalars(r), "chunk_ids": list(r.get("chunk_ids") or [])})
        for d in r.get("documents") or []:
            if isinstance(d, dict):
                out.documents.append(_document_row(d))


def _chunk_rows(p, out):
    for r in main_list(p):
        if not isinstance(r, dict):
            continue
        chunk = r.get("chunk") if isinstance(r.get("chunk"), dict) else {}
        if isinstance(chunk.get("text"), str):
            doc = doc_ref(r.get("doc") or r.get("document") or {"id": chunk.get("doc_id")})
            out.evidence.append(_evidence("chunk", chunk["text"], None, chunk.get("id"), None, doc))


def _observations(p, out):
    for r in main_list(p):
        if isinstance(r, dict):
            row = r.get("observation") if isinstance(r.get("observation"), dict) else r
            out.facts.append({"kind": "observation", "row": scalars(row), "chunk_ids": [row.get("chunk_id")] if row.get("chunk_id") else []})


def _document_history(p, out):
    for r in main_list(p):
        if isinstance(r, dict):
            out.facts.append({"kind": "document_history", "row": {"document": doc_ref(r.get("document")),
                                                                  "retired": r.get("retired"), "summary": r.get("summary"),
                                                                  "dropped_keys_count": len(r.get("dropped_keys") or [])},
                              "chunk_ids": []})


def _sql(p, out):
    for r in main_list(p):
        if isinstance(r, dict):
            out.facts.append({"kind": "sql_row", "row": scalars(r), "chunk_ids": []})


def _meta(p, out):
    body = {k: v for k, v in p.items() if k not in ("command", "warnings", "next")}
    out.facts.append({"kind": "meta", "row": scalars(body), "chunk_ids": []})


def _insights(p, out):
    for f in p.get("findings") or []:
        if isinstance(f, dict):
            out.guides.append({"kind": "insight", "key": f.get("key"), "title": f.get("title"), "score": f.get("score"),
                               "subjects": [s.get("_id") for s in f.get("subjects") or [] if isinstance(s, dict)][:10]})


def _generic(p, out):
    for r in main_list(p):
        if isinstance(r, dict):
            out.facts.append({"kind": "row", "row": scalars(r), "chunk_ids": []})


ADAPTERS = {
    "query vector-text": _vector_text, "query chunks": _chunk_rows, "query survey": _survey,
    "query entity-context": _entity_context, "query entity-resolve": _entity_rows,
    "query graph pattern1": _entity_rows, "query graph pattern2": _entity_rows, "query graph pattern7": _entity_rows,
    "query graph pattern5": _generic, "query graph pattern6": _generic, "query graph hierarchy": _generic,
    "query graph conflicts": _generic, "query graph text2cypher": _generic, "query graph filing-listing": _generic,
    "query graph pattern10": _chunk_rows, "query entity-history": _observations,
    "query document-history": _document_history, "query text2sql": _sql, "db sql": _sql,
    "db bridge": _meta, "db schema": _meta, "query domains-overview": _meta,
    "query insights list": _insights, "query views run": _generic,
}


def adapt(payload) -> Rows:
    out = Rows()
    if not isinstance(payload, dict):
        return out
    ADAPTERS.get(str(payload.get("command") or ""), _generic)(payload, out)
    return out
