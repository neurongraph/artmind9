"""The `:Observation` record — what one chunk of one document version asserted
about one thing — and the pure key function the projection aggregates on.

Vocabulary in `CONTEXT.md`; mechanism in `docs/projection-pipeline.md`.

Three invariants live here and are worth stating before the code, because each
one replaces a defect the redesign exists to remove:

1. **`name` is verbatim what the chunk said, and is never overwritten.**
   `canonical_name` is a *separate* property, and it is the one the key
   function consumes. Provenance fidelity is the entire reason observations
   exist -- an observation whose name has been rewritten to match its
   neighbours can no longer testify to what its document actually said.

2. **`entity_class` is a property, never a label, and an Observation never
   carries `:Entity`.** `graph_metadata` reports node types via
   `UNWIND labels(n)`, `text2cypher` builds its prompt from that output and
   instructs the model to write `(p:PERSON)`, and `entity_listing` derives an
   entity's class from its *label*. A `:POLICY` label on an observation would
   make a generated `MATCH (p:POLICY)` return superseded facts to a model that
   was told the label was safe. Omitting `:Entity` is also what structurally
   keeps observations out of `entity_embedding` and `entity_name_ft` -- no
   predicate to forget, no index to filter.

3. **Ids are deterministic.** An observation id is a hash of
   (chunk, canonical name, class, domain) and an entity id is a hash of the
   aggregate key, so re-writing the same content cannot duplicate, and a full
   rebuild from scratch reproduces byte-identical ids. Entities are therefore
   `MERGE`d, never deleted-and-recreated: `elementId` churn would break every
   external reference.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from loguru import logger

# Separators that introduce a "tail" a name may carry: an em/en/hyphen dash
# surrounded by whitespace, or a colon followed by whitespace. Whitespace is
# required so that "£10,001-£50,000" and "SAV-001" are single tokens rather
# than a name plus a tail.
_TAIL_SEPARATOR_RE = re.compile(r"\s+[-–—]\s+|:\s+")
# A trailing parenthetical, with any trailing punctuation after it.
_TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)[\s.,;:]*$")
_TRAILING_PUNCT_RE = re.compile(r"[\s.,;:!?\-–—]+$")
_DIGIT_RE = re.compile(r"\d")

# Properties artmind owns on an Observation. Extraction must never emit these;
# `build_observation` drops any that arrive from the model (the meta-schema's
# reserved `_` prefix, enforced at the write rather than trusted).
RESERVED_PREFIX = "_"

# Identity/structural keys that are written explicitly and must not be
# duplicated into the flattened domain-property bag.
_STRUCTURAL_KEYS = frozenset(
    {
        "id",
        "name",
        "canonical_name",
        "key",
        "entity_class",
        "domain",
        "type",
        "description",
        "context",
        "aliases",
        "doc_id",
        "doc_version",
        "chunk_id",
    }
)


# ── the key function ─────────────────────────────────────────────────────────


def normalize_name(value: str | None) -> str:
    """Normalize a canonical name to its aggregate-key form. Pure.

    Layers, in order:

    1. NFKC -- folds full-width and compatibility forms to one representation.
    2. casefold -- stronger than `.lower()`, and correct for non-ASCII.
    3. collapse whitespace -- including the non-breaking spaces that arrive
       from copied tables.
    4. strip trailing punctuation.
    5. strip a dash/colon tail, or a trailing parenthetical, **only if it
       contains a digit.**

    Layer 5 is the measurement-tail rule and it is deliberately narrow. A
    recurrent class names a thing that persists and changes, so a value or a
    date embedded in its name is not part of its identity:

        "SmartSaver Account Tier 2 Rate - 4.60% AER, effective 2026-02-01"
            -> "smartsaver account tier 2 rate"

    The digit condition is what keeps it from eating meaning. A tail with no
    digit is a genuine qualifier and is preserved:

        "Financial Conduct Authority (FCA)"  -> "financial conduct authority (fca)"
        "Overdraft Rate - Arranged"          -> "overdraft rate - arranged"

    Applied repeatedly until stable, so a name carrying both a parenthetical
    and a dash tail loses both.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = " ".join(text.split())
    text = _TRAILING_PUNCT_RE.sub("", text)

    # Iterate: a name may carry a parenthetical *and* a dash tail.
    for _ in range(8):
        before = text

        paren = _TRAILING_PAREN_RE.search(text)
        if paren and _DIGIT_RE.search(paren.group(0)):
            text = text[: paren.start()].rstrip()

        separator = _TAIL_SEPARATOR_RE.search(text)
        if separator and _DIGIT_RE.search(text[separator.end():]):
            text = text[: separator.start()].rstrip()

        text = _TRAILING_PUNCT_RE.sub("", text)
        if text == before:
            break

    return " ".join(text.split())


def aggregate_key(canonical_name: str | None, entity_class: str, domain: str) -> tuple[str, str, str]:
    """The aggregate key: normalized canonical name, class, domain.

    Purely computed -- it never depends on stored state. Every judgment call
    about whether two keys denote one thing lives in a same-as group instead
    (see `artmind.same_as`), which is what keeps a rebuild deterministic.
    """
    return (normalize_name(canonical_name), entity_class or "", domain or "")


def key_string(key: tuple[str, str, str]) -> str:
    """The key rendered for storage and hashing: `name|class|domain`."""
    return "|".join(key)


def entity_id(key: tuple[str, str, str]) -> str:
    """`sha256(canonical_name | entity_class | domain)`.

    Deterministic, so dropping the whole projection and rebuilding it from
    observations reproduces byte-identical ids. Entities are MERGEd on this --
    never deleted and recreated.
    """
    return hashlib.sha256(key_string(key).encode("utf-8")).hexdigest()


def observation_id(chunk_id: str, canonical_name: str | None, entity_class: str, domain: str) -> str:
    """`sha256(chunk_id | canonical_name | entity_class | domain)`.

    Scoped to the chunk, so one document version asserting the same thing in
    three chunks yields three observations -- each testifying to its own
    passage -- while re-writing the same chunk cannot duplicate.
    """
    payload = "|".join([chunk_id or "", normalize_name(canonical_name), entity_class or "", domain or ""])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def relation_observation_id(chunk_id: str, source_observation_id: str, rel_type: str, target_observation_id: str) -> str:
    """`sha256(chunk_id | source observation id | rel_type | target observation id)`.

    The raw, immutable record of one extracted relationship -- the relationship
    analogue of `observation_id`. An `ASSERTS_RELATION` edge between two
    `:Observation` nodes, never merged or patched, so a re-write of the same
    chunk cannot duplicate it. The projection rebuild is what turns these into
    aggregate `:Entity`-to-`:Entity` `RELATES_TO` edges -- see
    `projection.py`'s relationship aggregation.
    """
    payload = "|".join([chunk_id or "", source_observation_id or "", rel_type or "", target_observation_id or ""])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# ── building observations ────────────────────────────────────────────────────


def flatten_domain_props(props: dict | None) -> dict:
    """Flatten extracted domain properties to Neo4j-storable values.

    Scalars and lists of scalars pass through; empty values are dropped. A
    nested object is **dropped with a warning** rather than serialized to a
    JSON string: `_neo4j_value`'s dict->JSON branch is what let JSON blobs into
    entity properties, and a blob is unqueryable, unmergeable by shape, and
    invisible to the property-key hygiene the scorecard tracks. Properties
    flatten or they don't exist.
    """
    out: dict = {}
    for raw_key, value in (props or {}).items():
        key = str(raw_key)
        if key.startswith(RESERVED_PREFIX):
            logger.debug("Observation: dropped reserved-prefix property {!r} from extraction", key)
            continue
        if key in _STRUCTURAL_KEYS:
            logger.debug("Observation: dropped structural key {!r} from domain properties", key)
            continue
        if value is None or value == "" or value == []:
            continue
        if isinstance(value, dict):
            logger.warning(
                "Observation: dropped nested-object property {!r} (JSON blobs are forbidden; "
                "declare it as flat scalars in the domain schema)", key
            )
            continue
        if isinstance(value, list):
            flat = [v for v in value if not isinstance(v, (dict, list)) and v not in (None, "")]
            if len(flat) != len(value):
                logger.warning(
                    "Observation: dropped {} nested/empty item(s) from list property {!r}",
                    len(value) - len(flat), key,
                )
            if flat:
                out[key] = flat
            continue
        out[key] = value
    return out


def build_observation(
    entity: dict,
    *,
    canonical_name: str,
    domain_props: dict | None,
    doc_id: str,
    doc_version: int,
    chunk_id: str,
    kind: str,
    doc_valid_from: str | None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    valid_time_source: str | None = None,
) -> dict:
    """Build one Observation's property map. Pure -- no session, no I/O.

    `entity` is one entry from a chunk's extracted `entities.json`;
    `canonical_name` comes from the per-document canonicalization pass.

    Two valid-time axes are carried, and conflating them is the modelling
    error this signature exists to prevent:

    - **`_valid_from` / `_valid_to`** -- *fact-level*. Inherited from the
      source document unless the fact carries its own dates (a `RATE_ENTRY`
      declaring `effective_date` maps to `valid_from` via its schema). This is
      what decides conflict vs. temporal variation.
    - **`_doc_valid_from`** -- *document-level*, always. This is what orders
      the winner. It is denormalized onto the observation so the rebuild needs
      no Document join and no schema access inside its transaction.

    `_kind` is likewise denormalized from the class's schema declaration at
    ingest time, for the same reason.
    """
    entity_class = entity.get("entity_class") or ""
    obs_domain = entity.get("domain") or ""
    key = aggregate_key(canonical_name, entity_class, obs_domain)

    props: dict = {
        "id": observation_id(chunk_id, canonical_name, entity_class, obs_domain),
        # Verbatim what the chunk said. Never overwritten by canonicalisation.
        "name": entity.get("name") or "",
        "canonical_name": canonical_name,
        "key": key_string(key),
        # A PROPERTY, never a label -- see this module's docstring, reason 2.
        "entity_class": entity_class,
        "domain": obs_domain,
        "doc_id": doc_id,
        "doc_version": doc_version,
        "chunk_id": chunk_id,
        "_kind": kind,
    }

    for field in ("type", "description"):
        value = entity.get(field)
        if value:
            props[field] = value
    for field in ("context", "aliases"):
        value = entity.get(field)
        if isinstance(value, str):
            value = [value]
        if value:
            props[field] = [str(v) for v in value if v not in (None, "")]

    if valid_from:
        props["_valid_from"] = valid_from
    elif doc_valid_from:
        props["_valid_from"] = doc_valid_from
    if valid_to:
        props["_valid_to"] = valid_to
    if valid_time_source:
        props["_valid_time_source"] = valid_time_source
    if doc_valid_from:
        props["_doc_valid_from"] = doc_valid_from

    props.update(flatten_domain_props(domain_props))
    return props


def class_kind(schema: dict, entity_class: str) -> str:
    """The declared `kind` of a class: `recurrent` or `occurrent`.

    Defaults to **occurrent** for a class the schema does not declare (an
    extractor drifting off the enumerated list). Occurrent is the conservative
    default: it makes two observations that disagree raise a `:Conflict`
    rather than silently being recorded as ordinary history. A missed conflict
    is invisible; a spurious one is reviewable.
    """
    declared = ((schema.get("entity_types") or {}).get(entity_class) or {}).get("kind")
    if declared in ("recurrent", "occurrent"):
        return declared
    if entity_class:
        logger.debug(
            "Observation: class {!r} declares no kind; defaulting to occurrent", entity_class
        )
    return "occurrent"
