"""The two anti-drift steps that sit either side of chunk extraction.

They attack different problems and neither substitutes for the other:

- **Retrieved name vocabulary** (before extraction) stops a *new document*
  inventing a fresh name for something already in the graph. It is an ANN
  search over `entity_embedding` for names already in use, restricted to
  **recurrent** classes — an occurrent entity is a completed point event, so
  showing the extractor existing event names invites it to fold two distinct
  incidents into one.
- **Per-document canonicalization** (after extraction) stops *one document*
  producing nine names for one thing. Chunks extract in parallel and cannot
  see each other, which is exactly the problem: 9 of the 11 spurious
  "Tier 2 rate" entities on the live corpus came from a single document.

It runs **once per document, after every chunk has extracted** — never per
chunk. A per-chunk call would see the same blind spot the chunks themselves
have and would cost N times as much to learn nothing.

Neither step may fail an ingest. A degraded vocabulary or a failed
canonicalization means names are less well reconciled, which the key
function still partially repairs and a later `sameas` group can finish. That
is a quality regression, not a correctness one — unlike the projection
rebuild, which fails its commit on purpose.
"""
from __future__ import annotations

from loguru import logger

# How many existing names to show the extractor. Enough to cover a document's
# subject area, small enough that the list does not crowd out the passage
# itself in the prompt.
VOCABULARY_LIMIT = 25
# The ANN is over-fetched and then filtered down to recurrent classes, since
# the vector index cannot express the class predicate itself.
_OVERFETCH = 6


def recurrent_classes(schema: dict) -> set[str]:
    """The classes a schema declares `kind: recurrent`."""
    return {
        cls
        for cls, decl in (schema.get("entity_types") or {}).items()
        if (decl or {}).get("kind") == "recurrent"
    }


def retrieve_vocabulary(
    session,
    *,
    domain: str,
    schema: dict,
    seed_text: str,
    embed_model: str,
    limit: int = VOCABULARY_LIMIT,
) -> list[dict]:
    """Names already in use near this document, for recurrent classes only.

    Returns `[{"name": ..., "entity_class": ...}, ...]`, empty on any failure
    — an empty vocabulary is the pre-redesign behaviour, so a down embedding
    service degrades extraction quality rather than blocking ingest.
    """
    classes = recurrent_classes(schema)
    if not classes or not seed_text.strip():
        return []

    try:
        from artmind.extraction import embed_text

        query_vector = embed_text(embed_model, seed_text[:4000])
    except Exception as e:
        logger.warning("Name vocabulary: embedding failed, extracting without it ({})", e)
        return []

    try:
        rows = session.run(
            """
            CALL db.index.vector.queryNodes('entity_embedding', $k, $vector)
            YIELD node, score
            WHERE node.entity_class IN $classes
              AND (node.domain = $domain OR node.domain STARTS WITH ($domain + '.'))
            RETURN node.name AS name, node.entity_class AS entity_class, score
            ORDER BY score DESC
            LIMIT $limit
            """,
            k=limit * _OVERFETCH,
            vector=query_vector,
            classes=sorted(classes),
            domain=domain,
            limit=limit,
        ).data()
    except Exception as e:
        logger.warning("Name vocabulary: ANN query failed, extracting without it ({})", e)
        return []

    vocabulary = [{"name": r["name"], "entity_class": r["entity_class"]} for r in rows if r.get("name")]
    logger.info(
        "Name vocabulary: {} existing name(s) across {} recurrent class(es)",
        len(vocabulary), len(classes),
    )
    return vocabulary


def render_vocabulary(vocabulary: list[dict]) -> str:
    """The vocabulary block appended to the entities prompt."""
    if not vocabulary:
        return ""
    by_class: dict[str, list[str]] = {}
    for item in vocabulary:
        by_class.setdefault(item["entity_class"], []).append(item["name"])
    lines = [f"  {cls}: " + " · ".join(sorted(set(names))) for cls, names in sorted(by_class.items())]
    return "\n".join(lines)


# ── the per-document canonicalization pass ──────────────────────────────────


def collect_names(entities: list[dict]) -> dict[str, list[str]]:
    """Distinct extracted names, grouped by class — the pass's input."""
    by_class: dict[str, list[str]] = {}
    for entity in entities:
        name = (entity.get("name") or "").strip()
        entity_class = entity.get("entity_class") or ""
        if not name or not entity_class:
            continue
        names = by_class.setdefault(entity_class, [])
        if name not in names:
            names.append(name)
    return by_class


def build_canonicalization_prompt(
    names_by_class: dict[str, list[str]], vocabulary: list[dict], schema: dict, meta: dict | None = None
) -> str:
    from artmind.prompt_builder import load_meta

    meta = meta if meta is not None else load_meta()
    template = (meta.get("prompt_templates") or {}).get("canonicalization")
    if not template:
        raise KeyError("meta.yaml declares no prompt_templates.canonicalization")

    kinds = {
        cls: ((schema.get("entity_types") or {}).get(cls) or {}).get("kind") or "occurrent"
        for cls in names_by_class
    }
    blocks = []
    for cls, names in sorted(names_by_class.items()):
        blocks.append(f"{cls}  (kind: {kinds[cls]})")
        blocks.extend(f"  - {n}" for n in names)
    existing = render_vocabulary(vocabulary)

    return (
        template.replace("{{EXTRACTED_NAMES}}", "\n".join(blocks))
        .replace("{{EXISTING_NAMES}}", existing or "  (none yet — this is the first document in this domain)")
    )


def _apply_mapping(names_by_class: dict[str, list[str]], mapping: dict) -> dict[str, str]:
    """Fold the model's output into `raw name -> canonical name`, defaulting
    any name the model omitted or mangled to itself."""
    resolved: dict[str, str] = {}
    for names in names_by_class.values():
        for name in names:
            resolved[name] = name

    if isinstance(mapping, list):
        mapping = {
            item.get("name"): item.get("canonical_name")
            for item in mapping
            if isinstance(item, dict) and item.get("name")
        }
    if not isinstance(mapping, dict):
        logger.warning("Canonicalization: unusable response shape {}; keeping extracted names", type(mapping))
        return resolved

    for raw, canonical in mapping.items():
        if raw in resolved and isinstance(canonical, str) and canonical.strip():
            resolved[raw] = canonical.strip()
    return resolved


def canonicalize_document(
    entities: list[dict],
    *,
    schema: dict,
    vocabulary: list[dict],
    model: str,
    debug_dir=None,
) -> dict[str, str]:
    """ONE LLM call over this document's own names. Returns `raw -> canonical`.

    Never raises: on any failure every name maps to itself, which is exactly
    the pre-redesign behaviour.
    """
    names_by_class = collect_names(entities)
    if not names_by_class:
        return {}

    total = sum(len(v) for v in names_by_class.values())
    try:
        from artmind.extraction import extract_with_retry

        prompt = build_canonicalization_prompt(names_by_class, vocabulary, schema)
        raw, ok = extract_with_retry("document_canonicalization", model, prompt, debug_dir)
        if not ok:
            logger.warning("Canonicalization: LLM call failed; keeping {} extracted name(s)", total)
            return _apply_mapping(names_by_class, {})
        resolved = _apply_mapping(names_by_class, raw)
    except Exception as e:
        logger.warning("Canonicalization: skipped ({}); keeping {} extracted name(s)", e, total)
        return _apply_mapping(names_by_class, {})

    changed = sum(1 for raw_name, canonical in resolved.items() if raw_name != canonical)
    distinct_before = len(resolved)
    distinct_after = len(set(resolved.values()))
    logger.info(
        "Canonicalization: {} name(s) -> {} canonical ({} rewritten)",
        distinct_before, distinct_after, changed,
    )
    return resolved
