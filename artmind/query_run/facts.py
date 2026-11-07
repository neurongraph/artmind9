"""Step facts (P3 spec §3.4): every step-predicate name, computed from one step's payload.

The single home of these rules: the executor, `plan replay --reach` and `plan replay --run` all
read them here. Private sets (`_docs`, `_cited`) carry document keys so `merge` can union them;
`public` drops them before anything is printed.
"""

from __future__ import annotations

import math

from artmind.cli_conventions import LIST_KEY

DOC_KEYS = ("document", "doc", "documents", "source_documents")
TABLE_KEY = "source_tables"   # text2sql: [{table, source_file}]
_FALLBACK_LISTS = ("rows", "results", "documents", "findings", "observations")
_K_OPTIONS = ("--topK", "--limit")


# ── documents in a payload (moved from query_plan/reach.py) ─────────────────

def step_documents(payload) -> list[dict]:
    """Every document dict in a step payload: values under `DOC_KEYS` (a dict or a list of dicts) and any
    dict with a `doc_id`, at any depth. Each is normalised to {path, source_path, name, id, title}."""
    found: list[dict] = []

    def add(d: dict) -> None:
        ref = {"path": d.get("path"), "source_path": d.get("source_path"), "name": d.get("name"),
               "id": d.get("id") if "id" in d else d.get("doc_id"), "title": d.get("title")}
        if any(v for v in ref.values()):
            found.append(ref)

    def walk(node, key=None) -> None:
        if isinstance(node, dict):
            if key == TABLE_KEY:
                if node.get("source_file"):
                    found.append({"path": node["source_file"], "source_path": None, "name": None,
                                  "id": None, "title": None})
                return
            if key in DOC_KEYS or "doc_id" in node:
                add(node)
            for k, v in node.items():
                walk(v, k)
        elif isinstance(node, list):
            for v in node:
                walk(v, key)

    walk(payload)
    return found


def doc_key(doc: dict) -> str | None:
    for field in ("id", "path", "source_path", "name", "title"):
        value = doc.get(field)
        if value:
            return str(value)
    return None


def main_list(payload) -> list:
    if not isinstance(payload, dict):
        return []
    declared = LIST_KEY.get(str(payload.get("command") or ""))
    for key in (declared, *_FALLBACK_LISTS):
        if key and isinstance(payload.get(key), list):
            return payload[key]
    return []


def _row_doc(row: dict) -> str | None:
    """The document key of a row carrying text: brief `doc`, full `document`, or a chunk's `doc_id`."""
    for key in ("doc", "document"):
        if isinstance(row.get(key), dict) and doc_key(row[key]):
            return doc_key(row[key])
    chunk = row.get("chunk") if isinstance(row.get("chunk"), dict) else {}
    return row.get("doc_id") or chunk.get("doc_id")


def _cited(payload) -> set[str]:
    """Documents of rows that carry text (passages, chunks), at any depth of the main list."""
    out: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            chunk = node.get("chunk") if isinstance(node.get("chunk"), dict) else {}
            if isinstance(node.get("text"), str) or isinstance(chunk.get("text"), str):
                key = _row_doc(node)
                if key:
                    out.add(str(key))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(main_list(payload))
    return out


def _option_int(argv, names) -> int | None:
    argv = list(argv or [])
    for i, token in enumerate(argv[:-1]):
        if token in names:
            try:
                return int(argv[i + 1])
            except ValueError:
                return None
    return None


def _relevances(rows) -> list:
    return [r.get("relevance") for r in rows[:2] if isinstance(r, dict)]


def empty() -> dict:
    return {"rows": 0, "rows_total": 0, "truncated": False, "distinct_documents": 0, "docs_cited": 0,
            "max_relevance": None, "score_drop": None, "payload_chars": 0, "persisted": False,
            "entity_docs": 0, "rows_below_k": 0, "twins": 0, "stale": False, "view_status": "",
            "history_matches": 0, "concentration": 0, "level_agreement": 0, "_docs": [], "_cited": []}


def step_facts(payload, argv) -> dict:
    out = empty()
    if not isinstance(payload, dict):
        return out
    rows = main_list(payload)
    out["rows"] = len(rows)
    total = payload.get("rows_total")
    out["rows_total"] = total if isinstance(total, int) else len(rows)
    out["truncated"] = bool(payload.get("truncated"))
    docs = {k for k in (doc_key(d) for d in step_documents(payload)) if k}
    cited = _cited(payload)
    out["_docs"], out["_cited"] = sorted(docs), sorted(cited)
    out["distinct_documents"], out["docs_cited"] = len(docs), len(cited)
    rel = _relevances(rows)
    if rel and isinstance(rel[0], (int, float)):
        out["max_relevance"] = rel[0]
    if len(rel) == 2 and all(isinstance(v, (int, float)) for v in rel):
        out["score_drop"] = round(rel[0] - rel[1], 4)
    if payload.get("command") == "query vector-text":  # the B2 needle rule reads the first ten rows
        from artmind.retrieval_eval import metrics

        stats = metrics.needle_stats(rows)
        out["distinct_documents"], out["score_drop"] = stats["distinct_documents"], stats["score_drop"]
    out["payload_chars"] = len(str(payload))
    first = rows[0] if rows and isinstance(rows[0], dict) else {}
    if payload.get("command") == "query entity-context":
        out["entity_docs"] = int(first.get("source_documents_total") or 0)
    elif payload.get("command") == "query entity-history":
        out["entity_docs"] = len({r.get("doc_id") for r in rows if isinstance(r, dict) and r.get("doc_id")})
    k = _option_int(argv, _K_OPTIONS)
    out["rows_below_k"] = int(k is not None and len(rows) < k)
    out["stale"] = bool(payload.get("stale"))
    out["view_status"] = str(payload.get("status") or "")
    out["history_matches"] = len(payload.get("history_matches") or [])
    return out


def merge(items: list[dict]) -> dict:
    """Facts of a `par`, `map` or `pick`: counts summed, documents unioned, flags or-ed."""
    out = empty()
    if not items:
        return out
    docs = set().union(*(set(f.get("_docs") or ()) for f in items))
    cited = set().union(*(set(f.get("_cited") or ()) for f in items))
    out.update(
        rows=sum(f["rows"] for f in items), rows_total=sum(f["rows_total"] for f in items),
        truncated=any(f["truncated"] for f in items), distinct_documents=len(docs), docs_cited=len(cited),
        payload_chars=sum(f["payload_chars"] for f in items), entity_docs=max(f["entity_docs"] for f in items),
        rows_below_k=int(all(f["rows_below_k"] for f in items)), stale=any(f["stale"] for f in items),
        history_matches=sum(f["history_matches"] for f in items), _docs=sorted(docs), _cited=sorted(cited),
    )
    rels = [f["max_relevance"] for f in items if isinstance(f["max_relevance"], (int, float))]
    out["max_relevance"] = max(rels) if rels else None
    return out


def public(f: dict) -> dict:
    return {k: v for k, v in f.items() if not k.startswith("_")}


_OPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<=": lambda a, b: a <= b,
        ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, ">": lambda a, b: a > b}


def number(value, name: str):
    """A missing numeric fact: `score_drop` reads as minus infinity (no clear winner, B2), others as 0."""
    if value is None:
        return -math.inf if name == "score_drop" else 0
    return value


def evaluate(pred, by_node: dict, default_node: str) -> bool:
    """A step predicate (`program.parse_predicate`) over facts by node id; an unqualified name reads
    `default_node`. A node that has no facts (skipped, failed) reads as `empty()`."""
    from artmind.query_plan import program as program_mod

    def ev(node) -> bool:
        if isinstance(node, program_mod.Cmp):
            have = (by_node.get(node.node or default_node) or empty()).get(node.name)
            kind = program_mod.PREDICATE_TYPES.get(node.name)
            if kind == "num":
                have = number(have, node.name)
            elif kind == "bool":
                have = bool(have)
            else:
                have = str(have or "")
            return bool(_OPS[node.op](have, node.value))
        if isinstance(node, program_mod.Not):
            return not ev(node.operand)
        if isinstance(node, program_mod.Or):
            return any(ev(o) for o in node.operands)
        return all(ev(o) for o in node.operands)

    return ev(pred.ast)
