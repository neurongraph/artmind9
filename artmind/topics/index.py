"""The topic index (spec 2026-11-02 §6): `:TopicPage` nodes derived from the page files.

The files in `<vault>/_Topics/` stay the source of truth; a node, its `CITES` /
`CITES_PASSAGE` / `ABOUT` edges and its embedding are rebuilt from them, as passages
are from staging. **A `:TopicPage` is never evidence**: it carries no Document,
DocChunk or DocPassage label and no `PART_OF`, and none of the retrieval indexes
behind `vector-text`, `survey` or `entity-resolve` include it. It is read only by the
catalogue's `assets` part and by `survey`'s `topics` block (`stale.survey_topics`).

Every writer here bumps the page domains' generation in the writing transaction
(`catalogue_state.bump`), so a catalogue cache sees the change with the change.
Pages hold private corpus content; nothing here logs it.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from loguru import logger

from artmind.catalogue import state as catalogue_state
from artmind.topics import store

TEXT_MAX = 2000

_HEADING = re.compile(r"^#{1,6}[ \t]+(.*?)[ \t]*$", re.MULTILINE)
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")

_READ_CYPHER = """
MATCH (p:TopicPage {slug: $slug})
RETURN p.topic AS topic, p.domains AS domains, p.built AS built, p.anchor_kind AS anchor_kind,
       p.text AS text, p.sources_version AS sources_version
"""

_UPSERT_CYPHER = """
MERGE (p:TopicPage {slug: $slug})
SET p.topic = $topic, p._domain = $domain, p.domains = $domains, p.built = $built,
    p.anchor_kind = $anchor_kind, p.text = $text, p.sources_version = $sources_version
"""

# A changed text leaves the old vector describing other words: drop it, and the
# embedding step writes the new one.
_DROP_EMBEDDING_CYPHER = """
MATCH (p:TopicPage {slug: $slug})
REMOVE p.embedding, p._embed_key
"""

_DROP_EDGES_CYPHER = """
MATCH (p:TopicPage {slug: $slug})-[r:CITES|CITES_PASSAGE|ABOUT]->()
DELETE r
"""

_CITES_CYPHER = """
MATCH (p:TopicPage {slug: $slug})
UNWIND $doc_ids AS did
MATCH (d:Document {id: did})
MERGE (p)-[:CITES]->(d)
"""

_CITES_PASSAGE_CYPHER = """
MATCH (p:TopicPage {slug: $slug})
UNWIND $passage_ids AS pid
MATCH (q:DocPassage {id: pid})
MERGE (p)-[:CITES_PASSAGE]->(q)
"""

_ABOUT_CYPHER = """
MATCH (p:TopicPage {slug: $slug})
MATCH (e:Entity {key: $key})
MERGE (p)-[:ABOUT]->(e)
"""

_DELETE_CYPHER = """
MATCH (p:TopicPage {slug: $slug})
WITH p, p.domains AS domains
DETACH DELETE p
RETURN domains
"""

_ALL_CYPHER = """
MATCH (p:TopicPage)
RETURN p.slug AS slug, p.domains AS domains, p.embedding IS NOT NULL AS has_embedding,
       p._embed_key AS embed_key
"""

_EMBEDDING_CYPHER = """
MATCH (p:TopicPage {slug: $slug})
SET p.embedding = $embedding, p._embed_key = $embed_key
"""


# ---------------------------------------------------------------- the record


def sources_version(meta: dict) -> str:
    """A hash of the frontmatter `sources`: edges are rewritten only when it moves."""
    sources = [s for s in meta.get("sources") or [] if isinstance(s, dict)]
    blob = json.dumps(sources, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _overview(body: str) -> str:
    """The `Overview` section of a page, else its opening text."""
    headings = list(_HEADING.finditer(body))
    for i, match in enumerate(headings):
        if match.group(1).strip().casefold().startswith("overview"):
            end = headings[i + 1].start() if i + 1 < len(headings) else len(body)
            return body[match.end():end]
    first = headings[0].start() if headings else len(body)
    return body[:first] if body[:first].strip() else body


def page_text(topic: str, body: str) -> str:
    """The topic plus the page's Overview (links reduced to their text), at most `TEXT_MAX`."""
    overview = _LINK.sub(r"\1", _overview(body))
    text = f"{topic}\n{' '.join(overview.split())}".strip()
    return text[:TEXT_MAX]


def page_record(page) -> dict:
    """What the index holds for one page (a `store.Page`)."""
    meta = page.meta
    domains = [str(d) for d in meta.get("domains") or []]
    sources = [s for s in meta.get("sources") or [] if isinstance(s, dict)]
    anchor = meta.get("anchor") or {}
    doc_ids = sorted({str(s["doc_id"]) for s in sources if s.get("doc_id")})
    passage_ids = sorted({str(p) for s in sources for p in s.get("passages") or []})
    topic = str(meta.get("topic") or page.slug)
    return {
        "slug": page.slug, "topic": topic, "domain": domains[0] if domains else "", "domains": domains,
        "built": str(meta.get("built") or ""), "anchor_kind": str(anchor.get("kind") or "topic"),
        "anchor_key": anchor.get("key") if anchor.get("kind") == "entity" else None,
        "text": page_text(topic, page.body), "sources_version": sources_version(meta),
        "doc_ids": doc_ids, "passage_ids": passage_ids,
    }


# ------------------------------------------------------------------ writers


def _same(old: dict, record: dict) -> bool:
    return (old.get("topic") == record["topic"] and list(old.get("domains") or []) == record["domains"]
            and old.get("built") == record["built"] and old.get("anchor_kind") == record["anchor_kind"]
            and old.get("text") == record["text"] and old.get("sources_version") == record["sources_version"])


def plan_page(tx, record: dict) -> str:
    """`created`, `updated` or `unchanged`: what writing `record` would do. Reads only."""
    rows = tx.run(_READ_CYPHER, slug=record["slug"]).data()
    if not rows:
        return "created"
    return "unchanged" if _same(rows[0], record) else "updated"


def upsert_page(tx, record: dict) -> str:
    """Write one page's node and edges, and bump its domains, in the caller's transaction.

    A node is rewritten only when something changed; its edges only when the sources did;
    its vector is dropped only when its text did. Returns `created`, `updated` or `unchanged`."""
    rows = tx.run(_READ_CYPHER, slug=record["slug"]).data()
    old = rows[0] if rows else None
    if old is not None and _same(old, record):
        return "unchanged"
    if old is not None and old.get("text") != record["text"]:
        tx.run(_DROP_EMBEDDING_CYPHER, slug=record["slug"])
    tx.run(_UPSERT_CYPHER, slug=record["slug"], topic=record["topic"], domain=record["domain"],
           domains=record["domains"], built=record["built"], anchor_kind=record["anchor_kind"],
           text=record["text"], sources_version=record["sources_version"])
    if old is None or old.get("sources_version") != record["sources_version"]:
        tx.run(_DROP_EDGES_CYPHER, slug=record["slug"])
        if record["doc_ids"]:
            tx.run(_CITES_CYPHER, slug=record["slug"], doc_ids=record["doc_ids"])
        if record["passage_ids"]:
            tx.run(_CITES_PASSAGE_CYPHER, slug=record["slug"], passage_ids=record["passage_ids"])
        if record["anchor_key"]:
            tx.run(_ABOUT_CYPHER, slug=record["slug"], key=record["anchor_key"])
    catalogue_state.bump(tx, _bump_domains(record["domains"], (old or {}).get("domains")))
    return "created" if old is None else "updated"


def _bump_domains(*groups) -> list[str]:
    return catalogue_state.normalise(d for g in groups for d in (g or []))


def delete_page(tx, slug: str) -> bool:
    """Detach-delete one page's node and bump its domains, in the caller's transaction."""
    rows = tx.run(_DELETE_CYPHER, slug=slug).data()
    if not rows:
        return False
    catalogue_state.bump(tx, _bump_domains(rows[0].get("domains")))
    return True


# ---------------------------------------------------------------- embedding


def _embedding_setup(embed=None, embed_key: str | None = None, cache=True):
    from artmind.embed_sweep import _model
    from artmind.embedding_cache import EmbeddingCache
    from artmind.extraction import embed_document, embed_key as make_key

    model = _model()
    key = embed_key or make_key(model)
    embed = embed or (lambda text: embed_document(model, text))
    if cache is True:
        cache = EmbeddingCache()
    return embed, key, cache


def embed_pages(session, records: dict, *, embed=None, embed_key: str | None = None, cache=True) -> tuple:
    """Embed the nodes of `records` (slug -> record) that have no vector or one cut under
    another embed key, through the embedding cache. Returns (embedded, failed)."""
    from artmind.embedding_cache import cached_embed

    if not records:
        return 0, 0
    embed, key, cache = _embedding_setup(embed, embed_key, cache)
    present = {r["slug"]: r for r in session.run(_ALL_CYPHER).data()}
    embedded = failed = 0
    for slug, record in sorted(records.items()):
        node = present.get(slug)
        if node is None or (node.get("has_embedding") and node.get("embed_key") == key):
            continue
        try:
            vector = cached_embed(cache, key, record["text"], embed)
        except Exception as exc:
            logger.warning("topic index: could not embed {}: {}", slug, exc)
            failed += 1
            continue
        if not vector:
            failed += 1
            continue
        session.run(_EMBEDDING_CYPHER, slug=slug, embedding=vector, embed_key=key)
        embedded += 1
    return embedded, failed


# -------------------------------------------------------------------- sweep


def _in_scope(page_domains, domains) -> bool:
    return not domains or any(store._related(str(a), b) for a in page_domains or [] for b in domains)


def topics_directory(directory: Path | None = None) -> Path | None:
    """The folder the sweep reads: `directory`, else the vault's `_Topics/` when it exists."""
    if directory is not None:
        return Path(directory)
    import paths

    found = getattr(paths, "TOPICS_DIR", None)
    return Path(found) if found and Path(found).is_dir() else None


def sweep(domains=None, dry_run: bool = False, *, directory: Path | None = None, session=None,
          embed=None, embed_key: str | None = None, cache=True) -> dict:
    """Upsert every page in scope, delete nodes whose file is gone, embed what is missing.

    Returns `{indexed, updated, deleted, embedded}` (plus `failed` when an embedding
    failed). With `dry_run` nothing is written; `embedded` counts the vectors a run
    would compute. `domains` limits the run to pages over a related domain."""
    from artmind.graph_query import neo4j_session, read_session

    domains = list(domains or [])
    folder = topics_directory(directory)
    pages = store.list_pages(folder) if folder else []
    records = {p.slug: page_record(p) for p in pages}
    records = {s: r for s, r in records.items() if _in_scope(r["domains"], domains)}

    def run(session) -> dict:
        nodes = {r["slug"]: r for r in session.run(_ALL_CYPHER).data()}
        gone = sorted(s for s, n in nodes.items() if s not in records and _in_scope(n.get("domains"), domains))
        statuses = {s: plan_page(session, r) for s, r in records.items()}
        updated = sorted(s for s, st in statuses.items() if st != "unchanged")
        if dry_run:
            _, key, _ = _embedding_setup(embed, embed_key, None)
            would = sum(1 for s, r in records.items() if statuses[s] != "unchanged" or s not in nodes
                        or not nodes[s].get("has_embedding") or nodes[s].get("embed_key") != key)
            return {"indexed": len(records), "updated": len(updated), "deleted": len(gone), "embedded": would}
        for slug in updated:
            session.execute_write(lambda tx, r=records[slug]: upsert_page(tx, r))
        for slug in gone:
            session.execute_write(lambda tx, s=slug: delete_page(tx, s))
        embedded, failed = embed_pages(session, records, embed=embed, embed_key=embed_key, cache=cache)
        out = {"indexed": len(records), "updated": len(updated), "deleted": len(gone), "embedded": embedded}
        if failed:
            out["failed"] = failed
        return out

    if session is not None:
        return run(session)
    with (read_session() if dry_run else neo4j_session()) as opened:
        return run(opened)


# ------------------------------------------------- single-page, never raises


def index_page(slug: str) -> dict:
    """Index one page after `topic build` / `refresh` wrote it. Never raises: the file is the
    source of truth, and `artmind topic index` repairs a page this could not reach."""
    from artmind.graph_query import neo4j_session

    try:
        page = store.read_page(store.topics_dir(None), slug)
        if page is None:
            return {"status": "missing"}
        record = page_record(page)
        with neo4j_session() as session:
            status = session.execute_write(lambda tx: upsert_page(tx, record))
            embedded, failed = embed_pages(session, {slug: record})
        out = {"status": status, "embedded": embedded}
        if failed:
            out["failed"] = failed
        return out
    except Exception as exc:
        logger.warning("topic index: {} not indexed: {}", slug, exc)
        return {"status": "not indexed", "error": str(exc), "next": "artmind topic index"}


def unindex_page(slug: str) -> dict:
    """Drop one page's node after `topic delete`. Never raises (see `index_page`)."""
    from artmind.graph_query import neo4j_session

    try:
        with neo4j_session() as session:
            deleted = session.execute_write(lambda tx: delete_page(tx, slug))
        return {"status": "deleted" if deleted else "absent"}
    except Exception as exc:
        logger.warning("topic index: {} not removed: {}", slug, exc)
        return {"status": "not removed", "error": str(exc), "next": "artmind topic index"}


def best_effort_sweep(domains=None) -> dict | None:
    """The sweep for `vault sync` and `projection rebuild --sweep`: None when there are no
    pages and no nodes could be stale (no `_Topics/` folder), an `error` record on failure."""
    if topics_directory() is None:
        return None
    try:
        return sweep(domains)
    except Exception as exc:
        logger.warning("topic index: sweep failed: {}", exc)
        return {"error": str(exc), "next": "artmind topic index"}
