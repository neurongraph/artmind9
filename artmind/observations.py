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


def normalize_identity(value: str | None) -> str:
    """Normalize an **explicit** identity to its aggregate-key form. Pure.

    Layers 1-3 of `normalize_name` only (NFKC, casefold, whitespace) -- never
    the measurement-tail rule. That rule exists because an LLM-extracted name
    may drag a value along with it; an identity a caller *declared* (a table
    row's `"Pooja Jain (002PZE744)"`, where the parenthetical is the whole
    point) carries no such noise, and stripping it would fold three different
    Pooja Jains onto one entity. Idempotent, and a no-op on a key string's
    already-normalized name part.
    """
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return " ".join(text.split())


#: Joins an occasion's name and day inside the key's name part: `tennis~2024-03-30`.
#: Never a fourth `|` part -- every key parser expects three (spec 2026-11-15 question 1).
OCCASION_SEP = "~"
_DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def aggregate_key(
    canonical_name: str | None, entity_class: str, domain: str, *,
    identity: str | None = None, occurs_on: str | None = None,
) -> tuple[str, str, str]:
    """The aggregate key: normalized canonical name, class, domain.

    Purely computed -- it never depends on stored state. Every judgment call
    about whether two keys denote one thing lives in a same-as group instead
    (see `artmind.same_as`), which is what keeps a rebuild deterministic.

    `identity`, when given, replaces the name part and is normalized with
    `normalize_identity` rather than `normalize_name` -- for sources whose
    identity is declared (`ingest table2graph`) or already stored (an existing
    Entity's `key`), not inferred from free text. It is used as is: `occurs_on`
    is ignored (a stored key already carries its day).

    `occurs_on`, a `YYYY-MM-DD` day (`occasion_day`), is appended to the name part
    for an `identity: occasion` class (spec 2026-11-15 §5.2): the same happening on
    two days is two entities.
    """
    if identity:
        return (normalize_identity(identity), entity_class or "", domain or "")
    name = normalize_name(canonical_name)
    if occurs_on and name:
        name = f"{name}{OCCASION_SEP}{str(occurs_on)[:10]}"
    return (name, entity_class or "", domain or "")


def split_occasion(name_part: str | None) -> tuple[str, str | None]:
    """`("tennis", "2024-03-30")` for `tennis~2024-03-30`; `(name_part, None)` otherwise.

    A name-identity name that itself ends in a literal `~YYYY-MM-DD` would be misread as
    an occasion; callers apply this only to occasion classes."""
    head, sep, day = (name_part or "").rpartition(OCCASION_SEP)
    if sep and _DAY_RE.fullmatch(day):
        return head, day
    return name_part or "", None


def occasion_day(valid_from: str | None, precision: str | None) -> str | None:
    """The day an occasion keys on: the first day of its `_valid_from`, unless that is only
    a default date (precision `none`, plan question 2) or not a dated day at all."""
    if not valid_from or precision == "none":
        return None
    day = str(valid_from)[:10]
    return day if _DAY_RE.fullmatch(day) else None


# ── occasion names carry no date (spec 2026-11-15 §5.2, plan question 5) ─────

_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_ORDINAL_DAY = r"\d{1,2}(?:st|nd|rd|th)?"
_WEEKDAY = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_RELATIVE_DAY = r"(?:today|tonight|yesterday|tomorrow|last night|this (?:morning|afternoon|evening))"
# A whole date, optionally after a weekday: ISO, dd/mm/yyyy, "30 March 2024", "March 30, 2024".
_FULL_DATE = (rf"(?:{_WEEKDAY},?\s+)?(?:\d{{4}}-\d{{2}}-\d{{2}}|\d{{1,2}}/\d{{1,2}}/\d{{4}}"
              rf"|{_ORDINAL_DAY}\s+(?:of\s+)?{_MONTH},?\s+\d{{4}}|{_MONTH}\s+{_ORDINAL_DAY},?\s+\d{{4}})")
# What a separator, "on" or brackets may introduce besides a whole date: a day without a
# year, "the 30th", a weekday, "today". A bare space introduces only a whole date or "today",
# so "Easter Sunday" and "Black Friday sale" keep their weekday.
_DAY_TOKEN = (rf"(?:{_FULL_DATE}|(?:{_WEEKDAY},?\s+)?(?:{_ORDINAL_DAY}\s+(?:of\s+)?{_MONTH}|{_MONTH}\s+{_ORDINAL_DAY}"
              rf"|the\s+\d{{1,2}}(?:st|nd|rd|th)(?:\s+of\s+{_MONTH})?)|{_WEEKDAY}|{_RELATIVE_DAY})")
# A bare ordinal is a day only after "on" ("on the 30th", "on 30th"): "Grand Prix - 1st" and
# "Match, 2nd" are places in a series.
_ON_TOKEN = rf"(?:{_DAY_TOKEN}|\d{{1,2}}(?:st|nd|rd|th))"
# A day number before the separator makes "28-30 March 2024" / "28 - 30 March 2024" a range, not
# a tail: no digit directly before the separator, and after "<1-2 digit number> <separator> " no
# bare day number that starts the next token. Longer numbers ("Room 101 - 2024-03-30") and other
# tokens ("Match 3 - 2024-03-30", "Gig 5 - Saturday 30 March 2024") still strip.
# "Tennis-2024-03-30" is a tail.
_RANGE_NEXT = r"\d{1,2}(?:st|nd|rd|th)?\s"
_AFTER_DAY_NUMBER = r"(?<![^\d]\d\s)(?<![^\d]\d\d\s)"
_AFTER_DAY_RANGE_OPEN = r"(?<![^\d]\d\s[-–—:,]\s)(?<![^\d]\d\d\s[-–—:,]\s)"
_OCCASION_TAIL_RE = re.compile(
    rf"(?:(?<!\d)(?:{_AFTER_DAY_NUMBER}|(?!\s*[-–—:,]\s*{_RANGE_NEXT}))\s*[-–—:,]\s*(?:on\s+{_ON_TOKEN}|{_DAY_TOKEN})"
    rf"|\s+on\s+{_ON_TOKEN}"
    rf"|\s*\(\s*(?:on\s+{_ON_TOKEN}|{_DAY_TOKEN})\s*\)"
    rf"|\s+(?<![\w-])(?:(?:{_AFTER_DAY_RANGE_OPEN}|(?!{_RANGE_NEXT})){_FULL_DATE}|{_RELATIVE_DAY}))\W*$",
    re.IGNORECASE,
)
_OCCASION_HEAD_RE = re.compile(rf"^\W*{_FULL_DATE}(?![\w-])\s*[-–—:,]?\s*", re.IGNORECASE)
_WORD_RE = re.compile(r"\w")
#: Longer names are kept as they are: no happening's name is this long, and the patterns'
#: cost grows with length.
OCCASION_NAME_MAX = 200


def strip_occasion_date(name: str | None) -> str:
    """An occasion's name without the date it happened on (spec 2026-11-15 §5.2). Pure.

    The day is part of an occasion's key (`aggregate_key(occurs_on=)`), so a date, weekday,
    ordinal ("on the 30th") or "today" in its name only splits one happening into several
    names. Strips a trailing one -- after a dash, colon, comma, "on", in brackets, or, for a
    whole date or "today", after a bare space -- and a leading whole date. Numbers that are
    not dates stay ("Flight AI 302", "Room 101", "Q3 review"), as does a weekday that is
    part of a name ("Easter Sunday"), and a bare ordinal ("Grand Prix - 1st"). Whitespace
    runs become one space. Idempotent; a name that is only a date, or longer than
    `OCCASION_NAME_MAX`, is returned unchanged, never emptied.
    """
    original = (name or "").strip()
    if len(original) > OCCASION_NAME_MAX:
        return original
    text = " ".join(original.split())
    for _ in range(8):
        before = text
        text = _OCCASION_TAIL_RE.sub("", text).strip()
        text = _OCCASION_HEAD_RE.sub("", text).strip()
        if text == before:
            break
    return text if _WORD_RE.search(text) else original


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


def observation_id(
    chunk_id: str, canonical_name: str | None, entity_class: str, domain: str, *,
    identity: str | None = None, occurs_on: str | None = None,
) -> str:
    """`sha256(chunk_id | canonical_name | entity_class | domain)`.

    Scoped to the chunk, so one document version asserting the same thing in
    three chunks yields three observations -- each testifying to its own
    passage -- while re-writing the same chunk cannot duplicate. `identity`
    replaces the name part exactly as in `aggregate_key`; `occurs_on` joins it
    as in `aggregate_key`, so one chunk's "Tennis" on two days is two
    observations, not one (spec 2026-11-15 §5.2).
    """
    if identity:
        name_part = normalize_identity(identity)
    else:
        name_part = normalize_name(canonical_name)
        if occurs_on and name_part:
            name_part = f"{name_part}{OCCASION_SEP}{str(occurs_on)[:10]}"
    payload = "|".join([chunk_id or "", name_part, entity_class or "", domain or ""])
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


# ── property-key normalization: one concept, one key ───────────────────────


def _number_variants(key: str) -> list[str]:
    """`key` with its last `_`-segment in the other grammatical number:
    `notable_characteristics` <-> `notable_characteristic`,
    `activities` <-> `activity`. Candidates only -- a caller keeps one only
    when the schema declares it."""
    head, _, last = key.rpartition("_")
    prefix = f"{head}_" if head else ""
    forms = set()
    if last.endswith("ies") and len(last) > 3:
        forms.add(last[:-3] + "y")
    if last.endswith("es") and len(last) > 2:
        forms.add(last[:-2])
    if last.endswith("s") and not last.endswith("ss") and len(last) > 1:
        forms.add(last[:-1])
    if last.endswith("y") and len(last) > 1:
        forms.add(last[:-1] + "ies")
    forms.update({last + "s", last + "es"})
    forms.discard(last)
    return [prefix + f for f in sorted(forms)]


def property_key_rules(schema: dict) -> tuple[dict[str, str], frozenset[str]]:
    """(aliases, declared keys) for `normalize_property_keys`.

    `aliases` is the schema's top-level `property_aliases:` map
    (`variant: canonical`), domain-wide; `declared` is every property name any
    class declares -- a key is the domain's vocabulary, not one class's."""
    aliases = {str(k): str(v) for k, v in (schema.get("property_aliases") or {}).items()}
    declared = frozenset(
        str(prop)
        for decl in (schema.get("entity_types") or {}).values()
        for prop in ((decl or {}).get("properties") or {})
    )
    return aliases, declared


#: Observation keys that are never domain properties: build_observation's own fields (a superset
#: of `schema_validate.COMMON_KEYS`) plus the provenance keys table2graph and `artmind update` add.
#: `_`-prefixed keys are always kept too.
OBSERVATION_OWN_KEYS = _STRUCTURAL_KEYS | {"source_kind", "created_by"}

#: The `own=` of `filter_declared` for a raw LLM property bag: only the structural keys, which
#: `flatten_domain_props` drops quietly anyway. `source_kind` / `created_by` are artmind's
#: provenance, never the model's, so a model-emitted one is an undeclared key and is dropped.
LLM_BAG_OWN_KEYS = _STRUCTURAL_KEYS


def _entity_types(schema) -> dict:
    types = schema.get("entity_types") if isinstance(schema, dict) else None
    return types if isinstance(types, dict) else {}


def _class_properties(decl) -> dict:
    props = decl.get("properties") if isinstance(decl, dict) else None
    return props if isinstance(props, dict) else {}


def declared_property_keys(schema: dict) -> dict[str, set[str]]:
    """{class: the property keys it declares} -- the one reading of "declared" (spec 2026-11-15 §2.2).
    A malformed schema (non-mapping `entity_types` or `properties`) declares nothing there."""
    return {
        str(cls): {str(p) for p in _class_properties(decl)}
        for cls, decl in _entity_types(schema).items() if isinstance(decl, dict)
    }


def declared_property_order(schema: dict, *, include_temporal: bool = True,
                            include_own: bool = True) -> dict[str, list[str]]:
    """{class: declared keys in schema order, `required: true` keys first}. A malformed schema
    (non-mapping `entity_types` or `properties`) gives {} / [] there instead of raising.
    `include_own=False` leaves out a declared key that is an observation's own field
    (`OBSERVATION_OWN_KEYS`: `context`, `description`, `domain`, ...), which is never a domain
    property on the observation (brief rows)."""
    out: dict[str, list[str]] = {}
    for cls, decl in _entity_types(schema).items():
        if not isinstance(decl, dict):
            continue
        items = [(str(p), pd if isinstance(pd, dict) else {}) for p, pd in _class_properties(decl).items()]
        items = [(p, pd) for p, pd in items if include_temporal or not pd.get("temporal")]
        items = [(p, pd) for p, pd in items if include_own or p not in OBSERVATION_OWN_KEYS]
        out[str(cls)] = ([p for p, pd in items if pd.get("required") is True]
                         + [p for p, pd in items if pd.get("required") is not True])
    return out


def filter_declared(obs: dict, entity_class: str, declared: dict[str, set[str]], *,
                    own: frozenset[str] = OBSERVATION_OWN_KEYS) -> tuple[dict, dict]:
    """(`obs` with only the class's declared domain keys, {dropped key: value}). `own` keys and
    `_` keys stay. The default suits a staged observation (retime), whose own fields are real; a
    raw LLM property bag (`ingest._build_observations`, `update.write_user_chat`) passes
    `own=LLM_BAG_OWN_KEYS`, so a model-emitted `source_kind` or `created_by` is dropped like any
    other undeclared key (structural and `_` keys are then dropped by `flatten_domain_props`)."""
    allowed = declared.get(entity_class, set())
    kept, dropped = {}, {}
    for k, v in obs.items():
        if k in allowed or k in own or str(k).startswith(RESERVED_PREFIX):
            kept[k] = v
        else:
            dropped[k] = v
    return kept, dropped


def canonical_property_key(key: str, aliases: dict[str, str], declared: frozenset[str]) -> str:
    """The key `key` is recorded under: its declared alias target; itself
    when declared; else a declared number variant of it; else itself."""
    if key in aliases:
        return aliases[key]
    if key in declared:
        return key
    return next((v for v in _number_variants(key) if v in declared), key)


def _merge_values(a, b):
    if a == b:
        return a
    merged: list = []
    for value in (a if isinstance(a, list) else [a]) + (b if isinstance(b, list) else [b]):
        if value not in merged:
            merged.append(value)
    return merged[0] if len(merged) == 1 else merged


def normalize_property_keys(props: dict, aliases: dict[str, str], declared: frozenset[str]) -> dict:
    """`props` with every domain-property key under its canonical name.

    The extractor names one concept several ways -- singular and plural
    (`notable_characteristic` 752x beside the declared
    `notable_characteristics` 351x on the live journal), or near-synonyms
    (`narrative_role` / `narrative_function`). Each spelling is a separate
    property to everything that reads the graph back, so a fact recorded
    under one is invisible to a query for the other. Deterministic: a key
    moves only onto a declared key or a schema-declared alias target, and two
    keys landing on one name have their values merged. `_`-prefixed and
    structural keys are never touched.
    """
    out: dict = {}
    for key, value in props.items():
        k = str(key)
        target = k if k.startswith(RESERVED_PREFIX) or k in _STRUCTURAL_KEYS else (
            canonical_property_key(k, aliases, declared)
        )
        out[target] = _merge_values(out[target], value) if target in out else value
    return out


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
    retracts: str | None = None,
    identity: str | None = None,
    valid_precision: str | None = None,
    valid_time_of_day: str | None = None,
    identity_kind: str | None = None,
) -> dict:
    """Build one Observation's property map. Pure -- no session, no I/O.

    `entity` is one entry from `extract_kg`'s in-memory `all_entities` list;
    `canonical_name` comes from the per-document canonicalization pass.

    `identity` -- an explicit identity for the aggregate key, bypassing
    `normalize_name`'s measurement-tail rule (see `aggregate_key`). Supplied
    by `ingest table2graph`, whose names are rendered from a declared template
    rather than extracted, and by `update confirm`'s link path, which records
    against an existing Entity's stored key.

    Two valid-time axes are carried, and conflating them is the modelling
    error this signature exists to prevent:

    - **`_valid_from` / `_valid_to`** -- *fact-level*. Inherited from the
      source document unless the fact carries its own dates (a `RATE_ENTRY`
      declaring `effective_date` maps to `valid_from` via its schema). This is
      what decides conflict vs. temporal variation.
    - **`_doc_valid_from`** -- *document-level*, always. This is what orders
      the winner. It is denormalized onto the observation so the rebuild needs
      no Document join and no schema access inside its transaction.

    `_valid_precision` (`temporal.PRECISIONS`; `none` for a default document date) and
    `_valid_time_of_day` (the time or qualifier text, never compared) travel with the
    fact-level window (spec 2026-11-15 §2).

    `identity_kind` -- the class's `schema_validate.class_identity`. For `occasion`, the
    key's name part carries the fact's day (`occasion_day` of the effective `_valid_from`
    and precision; spec 2026-11-15 §5.2). Ignored when `identity` is given.

    `_kind` is likewise denormalized from the class's schema declaration at
    ingest time, for the same reason.

    `retracts` -- an existing `Observation.id` (or `ASSERTS_RELATION.id`) this
    observation declares no longer true. Declarative, not a mutation: the
    rebuild demotes the *named* observation to `:ObservationHistory` (or
    deletes the named relation edge) before merging -- the retracting
    observation itself is an ordinary, immutable record like any other. See
    `projection.apply_retractions`.
    """
    entity_class = entity.get("entity_class") or ""
    obs_domain = entity.get("_domain") or ""
    effective_from = valid_from or doc_valid_from
    occurs_on = occasion_day(effective_from, valid_precision) if identity_kind == "occasion" else None
    key = aggregate_key(canonical_name, entity_class, obs_domain, identity=identity, occurs_on=occurs_on)

    props: dict = {
        "id": observation_id(chunk_id, canonical_name, entity_class, obs_domain, identity=identity,
                             occurs_on=occurs_on),
        # Verbatim what the chunk said. Never overwritten by canonicalisation.
        "name": entity.get("name") or "",
        "canonical_name": canonical_name,
        "key": key_string(key),
        # A PROPERTY, never a label -- see this module's docstring, reason 2.
        "entity_class": entity_class,
        # `_`-prefixed like Entity._domain (graph_query.domain_predicate) --
        # every label uses this one name now, all the way from the entity
        # dict ingest.py hands in (its own "_domain" key, set once per
        # document/chunk) through to this Observation property.
        "_domain": obs_domain,
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
    if valid_precision:
        props["_valid_precision"] = valid_precision
    if valid_time_of_day:
        props["_valid_time_of_day"] = valid_time_of_day
    if doc_valid_from:
        props["_doc_valid_from"] = doc_valid_from
    if retracts:
        props["_retracts"] = retracts

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
