"""Entity description consolidation: regenerate clean prose from source chunks.

Descriptions accumulate at ingest as pipe-joined per-chunk fragments (see
ingest._upsert_entity), which makes them noisy for entity-resolve embeddings,
fulltext search, and answer synthesis. This module rewrites each entity's
description from the TEXT OF ITS SOURCE CHUNKS (via EXTRACTED_FROM), not by
editing the fragment blob — which gives exact provenance and temporal truth:

- non-destructive: the original pre-consolidation blob is preserved once in
  description_raw (never overwritten on re-runs)
- provenance: input chunk ids and their document names are recorded in
  description_source_chunks / description_source_docs — the reference-document
  lineage for the skills layer, with chunk-level detail underneath
- temporal: excerpts from superseded chunks are marked HISTORICAL in the
  prompt and must not be blended in as current facts
- conflict-safe: entities carrying an open Conflict node are skipped, and the
  prompt requires disagreeing values to be kept side by side with attribution
- idempotent: an entity is re-consolidated only when its chunk set differs
  from the recorded description_source_chunks (or --force)

After writing, embeddings are nulled and backfilled so entity-resolve's
vector leg immediately reflects the new text.
"""
from datetime import date, datetime, timezone

from loguru import logger

from artmind.extraction import call_llm, parse_json_response
from artmind.graph_query import neo4j_session
from utils.functions import load_env, resolve_llm_model


_SELECT_ENTITIES_CYPHER = """
MATCH (e:Entity)
WHERE (e.domain = $domain OR e.domain STARTS WITH ($domain + '.'))
  AND e.description IS NOT NULL
  AND ($nameFilter IS NULL OR toLower(e.name) CONTAINS toLower($nameFilter))
OPTIONAL MATCH (e)-[:EXTRACTED_FROM]->(c:DocChunk)
OPTIONAL MATCH (c)-[:PART_OF]->(d:Document)
WITH e, collect(c { .id, .text, .valid_to, doc_name: d.name, doc_domain: d.domain }) AS chunks
OPTIONAL MATCH (co:Conflict)-[:CONFLICT_OF]->(e)
WHERE co.status = 'open'
RETURN e.id AS id, e.name AS name, e.entity_class AS entity_class,
       e.description AS description,
       e.description_source_chunks AS prev_sources,
       count(co) > 0 AS has_open_conflict,
       chunks
ORDER BY e.name
"""

# description_raw is written with coalesce so only the FIRST consolidation
# captures the original fragment blob; re-runs never clobber it.
_WRITE_CYPHER = """
MATCH (e:Entity {id: $id})
SET e.description_raw = coalesce(e.description_raw, e.description),
    e.description = $description,
    e.description_consolidated_at = $now,
    e.description_source_chunks = $chunkIds,
    e.description_source_docs = $docNames,
    e.embedding = null
"""


def fragment_count(description: str) -> int:
    return len([f for f in (description or "").split(" | ") if f.strip()])


def classify_entity(
    row: dict, min_fragments: int, force: bool
) -> str:
    """Decide what to do with one entity row: 'consolidate' or a skip reason."""
    if row.get("has_open_conflict"):
        return "skipped_open_conflict"
    chunks = [c for c in (row.get("chunks") or []) if c and c.get("text")]
    if not chunks:
        return "skipped_no_chunks"
    if force:
        return "consolidate"
    prev = row.get("prev_sources")
    if prev is not None:
        current_ids = {c["id"] for c in chunks}
        return "skipped_unchanged" if set(prev) == current_ids else "consolidate"
    if fragment_count(row.get("description", "")) < min_fragments:
        return "skipped_below_threshold"
    return "consolidate"


def _is_current(chunk: dict, today: str) -> bool:
    valid_to = chunk.get("valid_to")
    return valid_to is None or valid_to > today


def select_excerpts(chunks: list[dict], max_chunks: int, today: str) -> list[dict]:
    """Pick up to max_chunks excerpts, current chunks first, ordered by id."""
    usable = [c for c in chunks if c and c.get("text")]
    usable.sort(key=lambda c: (not _is_current(c, today), c["id"]))
    return usable[:max_chunks]


def build_consolidation_prompt(
    name: str,
    entity_class: str,
    description: str,
    excerpts: list[dict],
    today: str,
    max_excerpt_chars: int = 1200,
) -> str:
    excerpt_blocks = []
    for i, c in enumerate(excerpts, start=1):
        flag = "" if _is_current(c, today) else "  [HISTORICAL — superseded]"
        doc = c.get("doc_name") or "unknown document"
        dom = c.get("doc_domain") or ""
        text = (c.get("text") or "")[:max_excerpt_chars]
        excerpt_blocks.append(f"[{i}] {doc} ({dom}){flag}\n{text}")
    excerpts_text = "\n\n".join(excerpt_blocks)
    return f"""\
You are maintaining a knowledge-graph entity catalog. Rewrite the entity's
description as clean, factual prose based ONLY on the source excerpts below.

RULES:
- 2-5 sentences, dense and specific. No repetition, no filler.
- Use only facts present in the excerpts (the current description is context,
  not a source of new facts).
- If excerpts disagree on a value (different timeframes, amounts, owners...),
  keep BOTH values side by side with their document names — never pick one
  and never average them.
- Excerpts marked HISTORICAL come from superseded documents: state such facts
  only as explicitly historical ("previously ..."), or omit them.
- Do not invent entities, numbers, dates, or causes not in the excerpts.

ENTITY: {name}  (class: {entity_class})

CURRENT DESCRIPTION (accumulated fragments, may be redundant):
{description}

SOURCE EXCERPTS:
{excerpts_text}

Respond with ONLY a JSON object (no markdown fencing, no explanation):
{{"description": "<the rewritten description>"}}
"""


def consolidate_descriptions(
    domain: str,
    name_filter: str | None = None,
    min_fragments: int = 2,
    max_chunks: int = 6,
    limit: int | None = None,
    model: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Consolidate entity descriptions for a domain (sub-domains rolled up)."""
    env = load_env()
    resolved_model = resolve_llm_model(env, model)
    today = date.today().isoformat()
    now = datetime.now(timezone.utc).isoformat()

    with neo4j_session() as session:
        rows = session.run(
            _SELECT_ENTITIES_CYPHER, domain=domain, nameFilter=name_filter
        ).data()

    counts: dict[str, int] = {}
    proposals: list[dict] = []
    for row in rows:
        decision = classify_entity(row, min_fragments, force)
        counts[decision] = counts.get(decision, 0) + 1
        if decision != "consolidate":
            continue
        if limit is not None and len(proposals) >= limit:
            counts["consolidate"] -= 1
            counts["skipped_over_limit"] = counts.get("skipped_over_limit", 0) + 1
            continue
        chunks = [c for c in row["chunks"] if c and c.get("text")]
        excerpts = select_excerpts(chunks, max_chunks, today)
        prompt = build_consolidation_prompt(
            row["name"], row["entity_class"], row["description"], excerpts, today
        )
        try:
            parsed = parse_json_response(call_llm(resolved_model, prompt))
            if isinstance(parsed, list):
                parsed = parsed[0] if parsed else {}
            new_description = (parsed or {}).get("description", "").strip()
        except Exception as exc:
            logger.warning("consolidate: LLM failed for {} ({}): {}", row["name"], row["id"], exc)
            counts["failed_llm"] = counts.get("failed_llm", 0) + 1
            counts["consolidate"] -= 1
            continue
        if not new_description:
            counts["failed_llm"] = counts.get("failed_llm", 0) + 1
            counts["consolidate"] -= 1
            continue
        proposals.append(
            {
                "id": row["id"],
                "name": row["name"],
                "entity_class": row["entity_class"],
                "old_description": (row["description"] or "")[:300],
                "new_description": new_description,
                # provenance: every chunk that fed the rewrite, and the
                # deduped reference-document list derived from them
                "source_chunks": [c["id"] for c in excerpts],
                "source_docs": sorted({c["doc_name"] for c in excerpts if c.get("doc_name")}),
            }
        )

    written = 0
    if not dry_run and proposals:
        with neo4j_session() as session:
            for p in proposals:
                session.run(
                    _WRITE_CYPHER,
                    id=p["id"],
                    description=p["new_description"],
                    now=now,
                    chunkIds=p["source_chunks"],
                    docNames=p["source_docs"],
                )
                written += 1

    embedded = 0
    if written:
        # embeddings were nulled above; backfill so entity-resolve's vector
        # leg reflects the new text (non-fatal if the embed service is down)
        try:
            from artmind.ingest import embed_entities_backfill

            embedded = embed_entities_backfill(domain)["entities_embedded"]
        except Exception as exc:
            logger.warning("consolidate: embedding backfill failed: {}", exc)

    return {
        "domain": domain,
        "command": "consolidate_descriptions",
        "model": resolved_model,
        "dry_run": dry_run,
        "examined": len(rows),
        "written": written,
        "entities_embedded": embedded,
        "counts": counts,
        "rows": proposals,
    }
