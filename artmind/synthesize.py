"""`projection synthesize` — rewrite an Entity's description from all of its
observations. See `docs/projection-pipeline.md` §3.

The one step in the pipeline that spends language-model budget without being
asked to, so it is always explicit — never per-document, never inside a
transaction, never automatic. Its input is `entity + its :Observation`s (never
`:ObservationHistory` — history is structurally excluded by construction,
since `read_latest_observations` only ever reads the `latest` label; there is
no HISTORICAL-marking step to port from the old chunk-based consolidation this
module replaces).

**Never nulls an embedding.** The embedding is computed FIRST, outside any
transaction — the same call ordering `docs/projection-pipeline.md` prescribes
for exactly this reason: `rebuild` can't call the embed service (it's inside a
transaction) so it only flags `embedding_stale`; `synthesize` already makes an
LLM call outside any transaction, so it computes the embedding before writing
anything, and then writes description + embedding TOGETHER in one transaction.
No null window, no transient stale window either — if the embed service is
down, the entity is skipped whole and reported; this command is explicit and
re-runnable, so nothing here is lost by skipping.

**Applies its own result in the same pass.** `synthesize_key` writes the
`:Synthesis` node AND calls `projection.rebuild_key` for that one key,
copying the text onto `Entity.description` immediately — otherwise the
workflow silently becomes rebuild -> synthesize -> rebuild, which nobody
remembers to do.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from loguru import logger

from artmind import projection
from artmind.extraction import call_llm, embed_text, parse_json_response
from artmind.graph_query import neo4j_session
from artmind.observations import entity_id, key_string
from utils.functions import load_env, resolve_llm_model

_SELECT_CYPHER = """
MATCH (e:Entity)
WHERE (e._domain = $domain OR e._domain STARTS WITH ($domain + '.'))
  AND e.key IS NOT NULL
  AND ($nameFilter IS NULL OR toLower(e.name) CONTAINS toLower($nameFilter))
OPTIONAL MATCH (co:Conflict {_source: 'projection'})-[:CONFLICT_OF]->(e) WHERE co.status = 'open'
OPTIONAL MATCH (s:Synthesis {id: e._id})
RETURN e._id AS id, e.key AS key, e.name AS name, e.entity_class AS entity_class,
       e._observation_count AS observation_count,
       e._observation_set_hash AS current_hash, s.observation_set_hash AS synth_hash,
       count(co) > 0 AS has_open_conflict
ORDER BY e.name
"""

# Structural/system observation keys — never rendered as a "fact" in the
# prompt. Mirrors projection.py's `_OBSERVATION_SYSTEM_KEYS` categorization,
# duplicated (not imported) to keep this module decoupled from the rebuild's
# internals; it is small and stable.
_SKIP_PROPS = frozenset({
    "id", "key", "canonical_name", "doc_id", "doc_version", "chunk_id",
    "_kind", "_valid_from", "_valid_to", "_doc_valid_from", "_valid_time_source",
    "_retracts", "name", "entity_class", "domain", "description",
    "source_kind", "created_by",
})


def classify_key(row: dict, min_observations: int, force: bool) -> str:
    """Decide what to do with one candidate entity row."""
    if row.get("has_open_conflict"):
        return "skipped_open_conflict"
    count = row.get("observation_count") or 0
    if count < min_observations:
        return "skipped_too_few_observations"
    if not force and row.get("synth_hash") and row.get("synth_hash") == row.get("current_hash"):
        return "skipped_unchanged"
    return "synthesize"


def build_synthesis_prompt(
    name: str, entity_class: str, observations: list[dict], max_observations: int = 20
) -> str:
    ordered = sorted(
        observations, key=lambda o: (o.get("_doc_valid_from") or "", o.get("id") or "")
    )[:max_observations]
    blocks = []
    for i, o in enumerate(ordered, start=1):
        lines = []
        if o.get("description"):
            lines.append(str(o["description"]))
        for prop_key, value in o.items():
            if prop_key in _SKIP_PROPS or prop_key.startswith("_") or value in (None, "", []):
                continue
            lines.append(f"{prop_key}: {value}")
        blocks.append(f"[{i}] " + "\n    ".join(lines) if lines else f"[{i}] (no content)")
    observations_text = "\n\n".join(blocks) or "(no observations)"
    return f"""\
You are maintaining a knowledge-graph entity catalog. Write the entity's
description as one coherent passage of clean, factual prose, drawn from ALL
the observations below — not just the wording of any single one.

RULES:
- 2-5 sentences, dense and specific. No repetition, no filler.
- Use only facts present in the observations below.
- If observations disagree on a value, keep BOTH values side by side — never
  pick one and never average them (that judgment belongs to a human, not this
  step).
- Do not invent entities, numbers, dates, or causes not in the observations.

ENTITY: {name}  (class: {entity_class})

OBSERVATIONS:
{observations_text}

Respond with ONLY a JSON object (no markdown fencing, no explanation):
{{"description": "<the rewritten description>"}}
"""


def synthesize_key(
    key: tuple[str, str, str], name: str, entity_class: str, *, model: str, embed_model: str
) -> dict:
    """One entity's full synthesize cycle. See module docstring for the
    embedding-safety and apply-in-same-pass invariants."""
    key_str = key_string(key)
    with neo4j_session() as session:
        observations = session.execute_read(
            lambda tx: projection.read_latest_observations(tx, key_str)
        )
    if not observations:
        return {"key": key_str, "name": name, "status": "skipped_no_observations"}

    prompt = build_synthesis_prompt(name, entity_class, observations)
    try:
        parsed = parse_json_response(call_llm(model, prompt))
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}
        text = (parsed or {}).get("description", "").strip()
    except Exception as exc:
        logger.warning("synthesize: LLM failed for {} ({}): {}", name, key_str, exc)
        return {"key": key_str, "name": name, "status": "failed_llm"}
    if not text:
        return {"key": key_str, "name": name, "status": "failed_llm"}

    observation_ids = sorted(o.get("id") or "" for o in observations)
    set_hash = hashlib.sha256("|".join(observation_ids).encode("utf-8")).hexdigest()

    # Compute the embedding FIRST, before any write — see module docstring.
    try:
        embedding = embed_text(embed_model, text)
    except Exception as exc:
        logger.warning(
            "synthesize: embedding failed for {} ({}); entity skipped whole "
            "(never null an embedding — re-run once the embed service is back): {}",
            name, key_str, exc,
        )
        return {"key": key_str, "name": name, "status": "failed_embedding"}

    eid = entity_id(key)
    synthesis = {
        "id": eid,
        "text": text,
        "observation_set_hash": set_hash,
        "observation_ids": observation_ids,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
    }

    def _write(tx):
        tx.run("MERGE (s:Synthesis {id: $id}) SET s = $props", id=eid, props=synthesis)
        outcome = projection.rebuild_key(tx, key, synthesis=synthesis)
        # rebuild_key already wrote description = synthesis["text"] and
        # flagged embedding_stale = true (the description changed). Overwrite
        # both here, in the SAME transaction — the embedding is never null
        # and never even transiently stale outside this transaction.
        tx.run(
            "MATCH (e:Entity {_id: $id}) SET e.embedding = $embedding, e.embedding_stale = false",
            id=eid, embedding=embedding,
        )
        return outcome

    with neo4j_session() as session:
        outcome = session.execute_write(_write)

    return {"key": key_str, "name": name, "status": "synthesized", "entity_outcome": outcome}


def synthesize(
    domain: str,
    name_filter: str | None = None,
    min_observations: int = 2,
    limit: int | None = None,
    model: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Synthesize entity descriptions for a domain (sub-domains rolled up)."""
    env = load_env()
    resolved_model = resolve_llm_model(env, model)
    embed_model = env.get("ARTMIND_KG_EMBEDDINGS_MODEL", "nomic-embed-text:latest")

    with neo4j_session() as session:
        rows = session.run(_SELECT_CYPHER, domain=domain, nameFilter=name_filter).data()

    counts: dict[str, int] = {}
    candidates: list[dict] = []
    for row in rows:
        decision = classify_key(row, min_observations, force)
        counts[decision] = counts.get(decision, 0) + 1
        if decision != "synthesize":
            continue
        if limit is not None and len(candidates) >= limit:
            counts["synthesize"] -= 1
            counts["skipped_over_limit"] = counts.get("skipped_over_limit", 0) + 1
            continue
        candidates.append(row)

    if dry_run:
        return {
            "domain": domain, "command": "projection_synthesize", "model": resolved_model,
            "dry_run": True, "examined": len(rows), "counts": counts,
            "candidates": [{"key": r["key"], "name": r["name"]} for r in candidates],
        }

    results = []
    synthesized = 0
    for row in candidates:
        key = tuple(row["key"].split("|"))
        if len(key) != 3:
            continue
        outcome = synthesize_key(
            key, row["name"], row["entity_class"], model=resolved_model, embed_model=embed_model
        )
        results.append(outcome)
        if outcome["status"] == "synthesized":
            synthesized += 1
        else:
            counts[outcome["status"]] = counts.get(outcome["status"], 0) + 1

    return {
        "domain": domain, "command": "projection_synthesize", "model": resolved_model,
        "dry_run": False, "examined": len(rows), "synthesized": synthesized,
        "counts": counts, "results": results,
    }
