"""The shape-audit bundle (spec 2026-10-11 §7.3): for a trace's evidence
chunks, the source text as numbered sentences, the subgraph the graph holds
for them (entities extracted from the chunks plus one RELATES_TO hop), the
staging entries extraction produced for them, and the schema sections of the
classes involved. Comparing staging with the graph shows whether a fact was
lost at extraction or at merge/commit. Read-only: graph reads, file reads.
"""

import json
import re
from pathlib import Path

from artmind import graph_query
from artmind.reviews import answerability

MAX_DEFAULT_CHUNKS = 8
PER_ENTITY_EDGES = 50
_CHUNK_SUFFIX = re.compile(r"_\d{3}$")
_SENTENCE_SPLIT = re.compile(r"\n+|(?<=[.!?])\s+")
_ENTITY_META = frozenset({"_id", "name", "entity_class", "label", "key", "_domain", "embedding", "_prop_sources"})

GRAPH_CYPHER = """
MATCH (c:DocChunk) WHERE c.id IN $chunk_ids AND c._domain IN $domains
MATCH (c)<-[:EXTRACTED_FROM]-(:Observation)<-[:AGGREGATES]-(e:Entity)
WITH DISTINCT e
OPTIONAL MATCH (e)-[r:RELATES_TO]-(n:Entity)
WITH e, collect(DISTINCT CASE WHEN r IS NULL THEN NULL ELSE
     {rel_type: r.rel_type, out: startNode(r) = e, other: n._id, other_name: n.name,
      other_class: n.entity_class, observation_count: r.observation_count} END)[0..$per_entity] AS rels
RETURN e {.*} AS entity, rels
"""


class ShapeError(ValueError):
    pass


def doc_id_of(chunk_id: str) -> str:
    return chunk_id.rsplit("_", 1)[0] if _CHUNK_SUFFIX.search(chunk_id) else chunk_id


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def find_staging(domain: str, doc_id: str) -> Path | None:
    import paths

    root = paths.KG_DIR / domain
    if not root.is_dir():
        return None
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        doc = _read_json(folder / "document.json", {})
        if doc.get("id") == doc_id or doc.get("artmind_id") == doc_id:
            return folder
    return None


def staging_for_chunk(domain: str, chunk_id: str) -> dict | None:
    folder = find_staging(domain, doc_id_of(chunk_id))
    if folder is None:
        return None
    doc = _read_json(folder / "document.json", {})
    chunk = next((c for c in _read_json(folder / "chunks.json", []) if c.get("id") == chunk_id), None)
    return {
        "folder": str(folder),
        "document": {k: doc.get(k) for k in ("id", "name", "path", "_valid_from", "_valid_to", "_valid_time_source")},
        "chunk": {"id": chunk_id, "text": (chunk or {}).get("text")},
        "observations": [o for o in _read_json(folder / "observations.json", []) if o.get("chunk_id") == chunk_id],
        "relationships": [r for r in _read_json(folder / "relationships.json", [])
                          if r.get("chunk_id") == chunk_id and r.get("rel_type") != "EXTRACTED_FROM"],
    }


def graph_for_chunks(domains: list[str], chunk_ids: list[str]) -> dict:
    with graph_query.read_session() as session:
        rows = session.run(GRAPH_CYPHER, chunk_ids=list(chunk_ids), domains=list(domains),
                           per_entity=PER_ENTITY_EDGES).data()
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}
    for row in rows:
        entity = graph_query.strip_internal_props(row["entity"])
        nid = entity.get("_id")
        nodes[nid] = {"id": nid, "name": entity.get("name"), "class": entity.get("entity_class"),
                      "properties": {k: v for k, v in entity.items() if k not in _ENTITY_META},
                      "extracted_here": True}
        for rel in row.get("rels") or []:
            other = rel["other"]
            nodes.setdefault(other, {"id": other, "name": rel.get("other_name"), "class": rel.get("other_class"),
                                     "properties": {}, "extracted_here": False})
            src, tgt = (nid, other) if rel.get("out") else (other, nid)
            edges.setdefault((src, tgt, rel.get("rel_type")),
                             {"source": src, "target": tgt, "rel_type": rel.get("rel_type"),
                              "properties": {"observation_count": rel.get("observation_count")}})
    return {"nodes": list(nodes.values()), "edges": list(edges.values())}


def sentences(text: str | None) -> list[dict]:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text or "") if p and p.strip()]
    return [{"n": i, "text": p} for i, p in enumerate(parts, 1)]


def _spans(shown: dict) -> list[dict]:
    return [s for e in shown["events"] for s in e.get("spans", [])] + list(shown.get("unattributed_spans") or [])


def default_chunks(shown: dict) -> list[str]:
    """The answer's cited chunks; else every productive span's evidence chunks."""
    from artmind.traces.path import _same_document, parse_sources

    productive = [s for s in _spans(shown) if s.get("exit_code") == 0 and (s.get("rows") or 0) > 0]
    cited = parse_sources(shown["turn"].get("answer"))
    cited_docs = set()
    for span in productive:
        for doc in (span.get("evidence") or {}).get("documents") or []:
            if doc.get("doc_id") and any(_same_document(c, doc.get("path") or "") for c in cited):
                cited_docs.add(doc["doc_id"])
    every, preferred = [], []
    for span in productive:
        for chunk in (span.get("evidence") or {}).get("chunks") or []:
            cid = chunk.get("chunk_id")
            if cid and cid not in every:
                every.append(cid)
                if chunk.get("doc_id") in cited_docs:
                    preferred.append(cid)
    return (preferred or every)[:MAX_DEFAULT_CHUNKS]


def resolve_target(trace_id: str | None, domain: str | None, chunk_ids) -> tuple[dict | None, str, list[str]]:
    """(the trace as `traces show` returns it, domain, chunk ids) for a review tool."""
    shown = None
    if trace_id:
        from artmind.traces.analysis import show

        shown = show(trace_id)
        if shown is None:
            raise ShapeError(f"no trace {trace_id}")
    if not domain and shown:
        domains = shown["turn"].get("domains") or []
        domain = domains[0] if len(domains) == 1 else None
        if domain is None and len(domains) > 1:
            raise ShapeError(f"the trace spans several domains ({', '.join(domains)}); pass --domain")
    if not domain:
        raise ShapeError("pass --domain (or a trace whose spans name one domain)")
    chunk_ids = list(chunk_ids) or (default_chunks(shown) if shown else [])
    if not chunk_ids:
        raise ShapeError("no evidence chunks found; pass --chunk")
    return shown, domain, chunk_ids


def bundle(*, trace_id: str | None = None, domain: str | None = None, chunk_ids=(), pattern=None) -> dict:
    import paths
    from artmind.temporal import load_schema

    shown, domain, chunk_ids = resolve_target(trace_id, domain, chunk_ids)
    staging = {cid: staging_for_chunk(domain, cid) for cid in chunk_ids}
    rows = {r["chunk"]["id"]: r for r in graph_query.chunks_by_id([domain], chunk_ids).get("rows") or []}
    text = []
    for cid in chunk_ids:
        row = rows.get(cid) or {}
        body = (row.get("chunk") or {}).get("text") or ((staging.get(cid) or {}).get("chunk") or {}).get("text")
        text.append({"chunk_id": cid, "document": (row.get("document") or {}).get("path"),
                     "sentences": sentences(body)})
    graph = graph_for_chunks([domain], chunk_ids)
    observations = [o for s in staging.values() if s for o in s["observations"]]
    relationships = [r for s in staging.values() if s for r in s["relationships"]]
    schema = load_schema(domain, schemas_dir=paths.DOMAIN_SCHEMAS_DIR)
    classes = {n.get("class") for n in graph["nodes"]} | {o.get("entity_class") for o in observations}
    types = schema.get("entity_types") or {}
    result = {
        "trace_id": trace_id,
        "domain": domain,
        "chunk_ids": chunk_ids,
        "question": shown["turn"]["question"] if shown else None,
        "expected": (shown.get("feedback") or {}).get("expected") if shown else None,
        "answer_basis": shown["turn"].get("answer_basis") if shown else None,
        "text": text,
        "graph": graph,
        "staging": staging,
        "schema": {"guidance": schema.get("guidance"),
                   "entity_types": {c: types[c] for c in sorted(c for c in classes if c in types)}},
    }
    if pattern is not None:
        result["answerability"] = {
            "graph": answerability.match(pattern, graph),
            "staging": answerability.match(pattern, answerability.from_staging(observations, relationships)),
        }
    return result
