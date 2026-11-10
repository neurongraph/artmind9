"""The documents a topic page reads (spec 2026-11-01 §3.2 step 2): the survey
ranking's top documents with their best passages, plus an entity anchor's
evidence chunks (their documents added while under the cap). Each document's
material is capped at MAP_INPUT_CHARS. Also the footnote metadata per source id."""
from __future__ import annotations

from dataclasses import dataclass, field

import paths
from artmind.graph_query import read_session
from artmind.topics import links as topic_links

MAX_PASSAGES_PER_DOC = 8
MAP_INPUT_CHARS = 10000

_LINKS = """
UNWIND $chunkIds AS cid
MATCH (c:DocChunk {id: cid})
OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
RETURN c.id AS chunk_id, c.header_path AS header_path, d.id AS doc_id, d.name AS name,
       coalesce(d.title, d.name) AS title, d.path AS path, d.source_path AS source_path,
       coalesce(d.content_sha256, d.fingerprint) AS version
"""

_DOCS = """
UNWIND $docIds AS did
MATCH (d:Document {id: did})
OPTIONAL MATCH (s:DocSummary {id: did + ':summary'})
RETURN d.id AS doc_id, coalesce(d.title, d.name) AS title, d.name AS name, d._domain AS domain, s.text AS summary
"""


@dataclass
class Source:
    id: str          # passage id, or chunk id for entity evidence
    chunk_id: str
    text: str


@dataclass
class Material:
    doc_id: str
    title: str | None
    name: str | None
    domain: str | None
    summary: str | None
    sources: list[Source] = field(default_factory=list)
    date: str | None = None
    status: str | None = None
    cells: list[str] = field(default_factory=list)   # faceted gather: the cells that found it

    def size(self) -> int:
        return sum(len(s.text) for s in self.sources)

    def add(self, source: Source) -> None:
        if any(s.id == source.id for s in self.sources):
            return
        if self.sources and self.size() + len(source.text) > MAP_INPUT_CHARS:
            return
        self.sources.append(source)


def gather(topic: str, domains: list[str], anchor: dict, max_docs: int, *, search=None,
           session_factory=None) -> tuple[list[Material], dict[str, dict]]:
    if search is None:
        from artmind.survey import survey_search as search
    payload = search(domains, topic, top_docs=max_docs, passages=MAX_PASSAGES_PER_DOC)
    materials: dict[str, Material] = {}
    for doc in payload.get("documents") or []:
        m = Material(doc["doc_id"], doc.get("title"), doc.get("name"), doc.get("domain"), doc.get("summary"))
        for p in doc.get("passages") or []:
            m.add(Source(p["id"], p["chunk_id"], p.get("text") or ""))
        materials[m.doc_id] = m
    evidence = (anchor.get("chunks") or []) if anchor.get("kind") == "entity" else []
    with (session_factory or read_session)() as session:
        new_docs = []
        for chunk in evidence:
            doc_id = chunk.get("doc_id")
            if doc_id and doc_id not in materials and len(materials) + len(new_docs) < max_docs and doc_id not in new_docs:
                new_docs.append(doc_id)
        if new_docs:
            for r in session.run(_DOCS, docIds=new_docs):
                r = dict(r)
                materials[r["doc_id"]] = Material(r["doc_id"], r.get("title"), r.get("name"), r.get("domain"),
                                                  r.get("summary"))
        for chunk in evidence:
            m = materials.get(chunk.get("doc_id"))
            if m is not None:
                m.add(Source(chunk["id"], chunk["id"], chunk.get("text") or ""))
        links = links_for(list(materials.values()), session)
    return list(materials.values()), links


def links_for(materials: list[Material], session) -> dict[str, dict]:
    """Footnote metadata per source id (title, paths, the document's version), one query."""
    chunk_ids = sorted({s.chunk_id for m in materials for s in m.sources})
    by_chunk = {r["chunk_id"]: dict(r) for r in session.run(_LINKS, chunkIds=chunk_ids)} if chunk_ids else {}
    links = {s.id: dict(by_chunk.get(s.chunk_id, {})) for m in materials for s in m.sources}
    vault = paths.ARTMIND_VAULT_DIR
    for info in links.values():
        if info.get("chunk_id") and info.get("doc_id"):
            info["chunk_file"] = topic_links.chunk_file(info["doc_id"], info["chunk_id"], vault)
    return links
