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

# Aliases shown under a vocabulary name: a few short ones. A long raw variant
# ("... - 4.70% AER (...)") teaches the extractor nothing about the name.
VOCABULARY_ALIASES = 3
VOCABULARY_ALIAS_CHARS = 60

# The per-name lookup (`lookup_name_candidates`): nearest existing names per
# extracted name, shown to the canonicalization pass.
NAME_LOOKUP_TOP = 3
NAME_LOOKUP_MAX_NAMES = 150
NAME_LOOKUP_MIN_SIM = 0.80
# ANN pool per name before the class/domain filter (the index cannot filter).
NAME_LOOKUP_POOL = 50

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
        from artmind.extraction import embed_query

        query_vector = embed_query(embed_model, seed_text[:4000])
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
            RETURN node.name AS name, node.entity_class AS entity_class,
                   coalesce(node.aliases, []) AS aliases, score
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

    vocabulary = [
        {"name": r["name"], "entity_class": r["entity_class"], "aliases": list(r.get("aliases") or [])}
        for r in rows if r.get("name")
    ]
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
    by_class: dict[str, dict[str, list[str]]] = {}
    for item in vocabulary:
        names = by_class.setdefault(item["entity_class"], {})
        names.setdefault(item["name"], [])
        for alias in vocabulary_aliases(item["name"], item.get("aliases")):
            if alias not in names[item["name"]]:
                names[item["name"]].append(alias)
    lines: list[str] = []
    for cls, names in sorted(by_class.items()):
        lines.append(f"  {cls}:")
        for name in sorted(names):
            lines.append(f"    - {name}")
            # Aliases on their own line, never on the name's line: a model copies
            # the name line, and anything on it can be read as part of the name.
            if names[name]:
                lines.append("        also called: " + " | ".join(names[name][:VOCABULARY_ALIASES]))
    return "\n".join(lines)


def vocabulary_aliases(name: str, aliases) -> list[str]:
    """The aliases worth showing beside `name`: short, distinct from the name,
    first `VOCABULARY_ALIASES` in stored order."""
    seen = {str(name or "").casefold()}
    out: list[str] = []
    for alias in aliases or ():
        text = str(alias or "").strip()
        if not text or len(text) > VOCABULARY_ALIAS_CHARS or text.casefold() in seen or "|" in text:
            continue
        seen.add(text.casefold())
        out.append(text)
        if len(out) == VOCABULARY_ALIASES:
            break
    return out


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
    names_by_class: dict[str, list[str]], vocabulary: list[dict], schema: dict, meta: dict | None = None,
    candidates: dict | None = None,
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
    closest = render_name_candidates(candidates or {})
    if closest:
        existing = (existing + "\n\n" if existing else "") + closest

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
    candidates: dict | None = None,
) -> dict[str, str]:
    """ONE LLM call over this document's own names. Returns `raw -> canonical`.

    `candidates` (from `lookup_name_candidates`) lists, per extracted name, the
    closest existing entity names; the prompt shows them under the vocabulary.

    Never raises: on any failure every name maps to itself, which is exactly
    the pre-redesign behaviour.
    """
    names_by_class = collect_names(entities)
    if not names_by_class:
        return {}

    total = sum(len(v) for v in names_by_class.values())
    try:
        from artmind.extraction import extract_with_retry

        prompt = build_canonicalization_prompt(names_by_class, vocabulary, schema, candidates=candidates)
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


# ── the per-name lookup, between extraction and canonicalization ───────────
#
# The vocabulary above is chosen from the document's opening text, so a name
# that first appears in a later chunk gets no existing name to fold onto. Once
# every chunk has extracted, each recurrent-class name is looked up on its own:
# an exact leg (key or alias equal to the name) and a vector leg over
# `entity_name_embedding` (name + short aliases). The results only reach the
# canonicalization prompt; the LLM still decides, and no call is added.

# Only this domain: the aggregate key carries the domain, so a name can only
# land on an entity of the document's own domain.
_EXACT_CYPHER = """
UNWIND $rows AS row
MATCH (e:Entity)
WHERE e._domain = $domain AND e.entity_class = row.entity_class
  AND any(a IN coalesce(e.aliases, []) WHERE toLower(a) = row.lower)
RETURN row.i AS i, e.name AS name, e.key AS key, 1.0 AS score
"""

_VECTOR_CYPHER = """
CYPHER 25
UNWIND $rows AS row
CALL (row) {
  MATCH (node:Entity)
    SEARCH node IN (
      VECTOR INDEX entity_name_embedding
      FOR row.vector
      LIMIT $k
    )
  WHERE node._domain = $domain AND node.entity_class = row.entity_class
  WITH node, vector.similarity.cosine(node.name_embedding, row.vector) AS score
  WHERE score >= $min_sim
  RETURN node.name AS name, node.key AS key, score
  ORDER BY score DESC
  LIMIT $top
}
RETURN row.i AS i, name, key, score
"""


def name_lookup_enabled() -> bool:
    """`ARTMIND_NAME_LOOKUP=off` turns the per-name lookup off (default on)."""
    from utils.functions import load_env

    return str(load_env().get("ARTMIND_NAME_LOOKUP") or "on").strip().lower() not in ("off", "0", "false", "no")


def name_lookup_min_sim() -> float:
    """`ARTMIND_NAME_LOOKUP_MIN_SIM` (0-1), default `NAME_LOOKUP_MIN_SIM`; a bad value is the default."""
    from utils.functions import load_env

    try:
        value = float(load_env().get("ARTMIND_NAME_LOOKUP_MIN_SIM") or NAME_LOOKUP_MIN_SIM)
    except (TypeError, ValueError):
        return NAME_LOOKUP_MIN_SIM
    return value if 0.0 < value <= 1.0 else NAME_LOOKUP_MIN_SIM


def lookup_name_candidates(
    session,
    entities: list[dict],
    *,
    domain: str,
    schema: dict,
    embed_model: str,
    embed=None,
    cache=None,
    top: int = NAME_LOOKUP_TOP,
    max_names: int = NAME_LOOKUP_MAX_NAMES,
    min_sim: float | None = None,
) -> dict[tuple[str, str], list[str]]:
    """`{(entity_class, extracted name): [existing name, ...]}` for recurrent classes.

    Alias hits first, then vector hits by score, at most `top` per name, all of
    the same class in the document's own domain. A candidate whose key is the
    name's own key is left out: the name lands on that entity anyway. Never raises; any failure yields what was found so far
    (possibly nothing).
    """
    from artmind.observations import aggregate_key, key_string

    classes = recurrent_classes(schema)
    if not classes:
        return {}
    wanted: list[tuple[str, str]] = []
    for cls, names in collect_names(entities).items():
        if cls in classes:
            wanted.extend((cls, n) for n in names)
    if not wanted:
        return {}
    if len(wanted) > max_names:
        logger.info("Name lookup: {} names, looking up the first {}", len(wanted), max_names)
        wanted = wanted[:max_names]

    own_keys = [key_string(aggregate_key(n, cls, domain)) for cls, n in wanted]
    found: dict[int, list[tuple[float, int, str, str]]] = {}

    def add(rows, rank: int) -> None:
        for r in rows:
            i = int(r["i"])
            if not r.get("name") or r.get("key") == own_keys[i]:
                continue
            found.setdefault(i, []).append((float(r.get("score") or 0.0), rank, r["name"], r.get("key") or ""))

    exact_rows = [{"i": i, "entity_class": cls, "lower": n.lower()}
                  for i, (cls, n) in enumerate(wanted)]
    try:
        add(session.run(_EXACT_CYPHER, rows=exact_rows, domain=domain).data(), 0)
    except Exception as e:
        logger.warning("Name lookup: exact leg failed ({}); continuing without it", e)

    try:
        from artmind.embedding_cache import cached_embed
        from artmind.extraction import embed_document, embed_key as _embed_key

        # Embedded the way `name_embedding` is (document prefix): a name is
        # compared with names, not a question with passages, and a name with no
        # aliases then shares its cached vector with the embed sweep.
        embed = embed or (lambda text: embed_document(embed_model, text))
        key = _embed_key(embed_model)
        vector_rows = [{"i": i, "entity_class": cls, "vector": cached_embed(cache, key, n, embed)}
                       for i, (cls, n) in enumerate(wanted)]
        vector_rows = [r for r in vector_rows if r["vector"]]
        if vector_rows:
            add(session.run(
                _VECTOR_CYPHER, rows=vector_rows, domain=domain, k=NAME_LOOKUP_POOL, top=top,
                min_sim=name_lookup_min_sim() if min_sim is None else min_sim,
            ).data(), 1)
    except Exception as e:
        logger.warning("Name lookup: vector leg failed ({}); continuing without it", e)

    out: dict[tuple[str, str], list[str]] = {}
    for i, hits in found.items():
        hits.sort(key=lambda h: (h[1], -h[0], h[2]))
        names: list[str] = []
        for _score, _rank, name, _key in hits:
            if name not in names and name != wanted[i][1]:
                names.append(name)
        if names:
            out[wanted[i]] = names[:top]
    logger.info("Name lookup: {} of {} name(s) have a close existing name", len(out), len(wanted))
    return out


def render_name_candidates(candidates: dict[tuple[str, str], list[str]]) -> str:
    """The per-name block appended under the vocabulary in the canonicalization
    prompt: each extracted name, then its closest existing names one per line."""
    if not candidates:
        return ""
    lines = ["  Closest existing names to each extracted name (reuse one only if it is the same thing):"]
    for (cls, name) in sorted(candidates):
        lines.append(f"  {cls} \"{name}\":")
        lines.extend(f"    - {existing}" for existing in candidates[(cls, name)])
    return "\n".join(lines)
