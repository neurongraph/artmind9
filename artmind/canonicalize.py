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

import unicodedata

from loguru import logger

# How many existing names to show the extractor. Enough to cover a document's
# subject area, small enough that the list does not crowd out the passage
# itself in the prompt.
VOCABULARY_LIMIT = 25
# The ANN is over-fetched and then filtered down to recurrent classes, since
# the vector index cannot express the class predicate itself.
_OVERFETCH = 6

# How many existing property keys to show the extractor, per entity class.
PROPERTY_VOCABULARY_LIMIT = 25

# Entity properties that are machinery, never a domain-declared property key
# an extraction prompt would want to offer back as "already in use". Mirrors
# `projection._SPECIAL_KEYS` plus the two preserved embedding fields; every
# genuinely system-owned key is `_`-prefixed by the Phase 4 convention and is
# excluded by that prefix instead of needing to be named here.
_RESERVED_ENTITY_KEYS = frozenset(
    {"name", "entity_class", "domain", "type", "description", "context", "aliases", "key", "embedding"}
)


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
            CYPHER 25
            MATCH (node:Entity)
              SEARCH node IN (
                VECTOR INDEX entity_embedding
                FOR $vector
                LIMIT $k
              )
            WHERE node.entity_class IN $classes
              AND (node._domain = $domain OR node._domain STARTS WITH ($domain + '.'))
            WITH node, vector.similarity.cosine(node.embedding, $vector) AS score
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
    """The vocabulary block appended to the entities prompt.

    **One name per line.** An earlier version joined them with `" · "`, and the
    live run showed the extractor reading a rendered line back as a single
    entity name:

        Bank of England Base Rate - 4.00%, effective 2026-01-15 · Next Rate
        Review - February 15, 2026

    That is two vocabulary entries glued together by the separator. Anything
    that can be mistaken for part of a name — an inline separator most of all —
    does not belong in a list a model is asked to copy names out of.
    """
    if not vocabulary:
        return ""
    by_class: dict[str, list[str]] = {}
    for item in vocabulary:
        by_class.setdefault(item["entity_class"], []).append(item["name"])
    lines: list[str] = []
    for cls, names in sorted(by_class.items()):
        lines.append(f"  {cls}:")
        lines.extend(f"    - {name}" for name in sorted(set(names)))
    return "\n".join(lines)


def retrieve_property_vocabulary(
    session,
    *,
    domain: str,
    schema: dict,
    limit: int = PROPERTY_VOCABULARY_LIMIT,
) -> dict[str, list[str]]:
    """Property keys already committed to the graph, per entity class — the
    properties-side counterpart to `retrieve_vocabulary`.

    Unlike names, property keys need no semantic retrieval: the live set per
    class is small and already bounded by the schema, so a plain aggregate
    over existing `:Entity` nodes is enough — no embedding call, no ANN.

    Scoped to the DOMAIN FAMILY (the top-level prefix, via `STARTS WITH`),
    deliberately wider than `retrieve_vocabulary`'s exact-domain scope: the
    near-dup keys found live recur across SIBLING domain files
    (`balance_minimum`/`balance_maximum` are declared identically in
    `banking.products`, `banking.reference`, `banking.cases`, and five other
    domain schemas), not confined to one domain the way a recurrent entity's
    own name is. See Finding B, docs/redesign-phase8-implementation-notes.md.

    Returns `{entity_class: [key, ...]}`, ordered by how often each key is
    already in use; empty on any failure — a degraded vocabulary costs
    extraction quality, never the ingest, the same fail-open contract as
    `retrieve_vocabulary`.
    """
    classes = sorted((schema.get("entity_types") or {}).keys())
    if not classes or not domain:
        return {}
    family = domain.split(".")[0]

    try:
        rows = session.run(
            """
            MATCH (e:Entity)
            WHERE e.entity_class IN $classes
              AND (e._domain = $family OR e._domain STARTS WITH ($family + '.'))
            UNWIND [k IN keys(e) WHERE NOT k STARTS WITH '_' AND NOT k IN $reserved] AS prop_key
            RETURN e.entity_class AS entity_class, prop_key AS key, count(*) AS uses
            ORDER BY uses DESC
            """,
            classes=classes, family=family, reserved=sorted(_RESERVED_ENTITY_KEYS),
        ).data()
    except Exception as e:
        logger.warning("Property vocabulary: query failed, extracting without it ({})", e)
        return {}

    vocabulary: dict[str, list[str]] = {}
    for row in rows:
        cls, key = row.get("entity_class"), row.get("key")
        if not cls or not key:
            continue
        keys = vocabulary.setdefault(cls, [])
        if key not in keys and len(keys) < limit:
            keys.append(key)

    if vocabulary:
        logger.info(
            "Property vocabulary: {} key(s) across {} class(es)",
            sum(len(v) for v in vocabulary.values()), len(vocabulary),
        )
    return vocabulary


def render_property_vocabulary(vocabulary: dict[str, list[str]]) -> str:
    """The property-key vocabulary block appended to the properties prompt.

    One class per block, one key per line — the same discipline
    `render_vocabulary` established for names and for the same reason: a
    model asked to copy a key out of a list must never see two keys glued
    together by an inline separator.
    """
    if not vocabulary:
        return ""
    lines: list[str] = []
    for cls, keys in sorted(vocabulary.items()):
        if not keys:
            continue
        lines.append(f"  {cls}:")
        lines.extend(f"    - {key}" for key in keys)
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


def _match_key(value: str) -> str:
    """A tolerant index key for matching the model's echoed name back to the
    name we actually sent it.

    Deliberately NOT `observations.normalize_name`: that strips measurement
    tails, so `"X - 4.70% AER"` and `"X - 5.25% AER"` would collide and one
    entry's rewrite would be applied to the other. This folds only the noise a
    model introduces when echoing a string back — case, unicode form,
    whitespace runs, dash variants, trailing punctuation — and keeps everything
    that distinguishes two names.

    The dash fold matters more than it looks: these names are full of em- and
    en-dashes, and a model that normalises them to a plain hyphen would
    otherwise have its entire response discarded.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("\u2014", "-").replace("\u2013", "-").replace("\u2212", "-")
    text = " ".join(text.split())
    return text.strip(" .,;:!?-")


def _apply_mapping(names_by_class: dict[str, list[str]], mapping: dict) -> dict[str, str]:
    """Fold the model's output into `raw name -> canonical name`, defaulting
    any name the model omitted or mangled to itself.

    Matching is exact first, then tolerant (`_match_key`). Exact-only matching
    silently discarded the rewrite whenever the model echoed a name back with a
    hyphen for an em-dash, a collapsed double space, or a trailing period —
    which is most of the time on names carrying `—`, `–` and `£`. The symptom
    was a canonicalization pass that appeared to run and changed nothing.
    """
    resolved: dict[str, str] = {}
    by_match_key: dict[str, str] = {}
    for names in names_by_class.values():
        for name in names:
            resolved[name] = name
            by_match_key.setdefault(_match_key(name), name)

    if isinstance(mapping, list):
        mapping = {
            item.get("name"): item.get("canonical_name")
            for item in mapping
            if isinstance(item, dict) and item.get("name")
        }
    if not isinstance(mapping, dict):
        logger.warning("Canonicalization: unusable response shape {}; keeping extracted names", type(mapping))
        return resolved

    unmatched = 0
    for raw, canonical in mapping.items():
        if not isinstance(canonical, str) or not canonical.strip():
            continue
        target = raw if raw in resolved else by_match_key.get(_match_key(raw))
        if target is None:
            unmatched += 1
            continue
        resolved[target] = canonical.strip()
    if unmatched:
        logger.warning(
            "Canonicalization: {} returned name(s) matched nothing this document extracted; ignored",
            unmatched,
        )
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
