"""The projection — `:Entity` rebuilt deterministically from `:Observation`.

Mechanism in `docs/projection-pipeline.md` §2. Two halves live here and the
split is load-bearing:

- **`merge_observations` and `affected_keys` are pure functions over dicts.**
  No session, no Cypher, no clock. Every judgment the projection makes -- who
  wins, what unions, what is a conflict and what is history, which keys get
  swept -- is decided here and is testable without a database. A bare
  `MagicMock()` Neo4j session returns a truthy result for *any* Cypher, so
  logic that lives inside a query is logic no test can actually check.
- **`rebuild` is the I/O.** It reads observations, calls the pure half, and
  writes the result.

The rebuild runs **inside the same transaction as the observation write**, and
a failure fails that commit. This is deliberately unlike the pre-redesign
temporal and supersession hooks, which caught their own exceptions and logged
a warning: a silently-skipped projection is a silently-stale query layer.
It also cannot call the embedding service (it is inside a transaction), which
is why it marks `embedding_stale` and **never nulls an embedding** -- a null
embedding is absent from `entity_embedding`, which makes the entity invisible
to `entity-resolve`'s vector leg rather than merely less accurate.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone

from loguru import logger

from artmind.observations import aggregate_key, entity_id, key_string, normalize_name

# `context` is unioned but capped -- it exists to give retrieval a few verbatim
# anchors, not to accumulate every passage that ever mentioned the entity.
CONTEXT_CAP = 12

# Observation properties that are machinery, not content. None of these is
# merged onto the Entity as a domain property.
_OBSERVATION_SYSTEM_KEYS = frozenset(
    {
        "id",
        "key",
        "canonical_name",
        "doc_id",
        "doc_version",
        "chunk_id",
        "_kind",
        "_valid_from",
        "_valid_to",
        "_doc_valid_from",
        "_valid_time_source",
        "_retracts",
    }
)
# Merged by their own named policy rather than by shape.
_SPECIAL_KEYS = frozenset({"name", "entity_class", "domain", "type", "description", "context", "aliases"})


# ── ordering ─────────────────────────────────────────────────────────────────


def _winner_sort_key(observation: dict) -> tuple:
    """Order observations so the last one is the winner.

    **The winner is the observation with the latest source-document
    `valid_from` -- NOT the highest `doc_version`.** Cross-document aggregates
    are the common case and in a healthy vault every document sits at version
    1, so `doc_version` supplies no ordering at all: sorting by it would leave
    three rate schedules tied and the "winner" decided by dict iteration order.

    `doc_version` and `id` are tiebreakers only, and `id` is there purely so
    the order is total and therefore reproducible.
    """
    return (
        observation.get("_doc_valid_from") or "",
        observation.get("doc_version") or 0,
        observation.get("id") or "",
    )


def _winner(observations: list[dict]) -> dict:
    return max(observations, key=_winner_sort_key)


# ── merge by shape ───────────────────────────────────────────────────────────


def _is_list(value) -> bool:
    return isinstance(value, (list, tuple, set))


def _union(values: list) -> list:
    """Order-preserving union, first-seen order — stable across rebuilds
    because the observations feeding it are sorted.

    Neo4j arrays must be homogeneously typed. Each individual observation's
    own value already passed `flatten_domain_props`, so a single observation
    never contributes a mixed bag — but two *different* observations can
    disagree on **type**, not just value, for the same property (one chunk
    extracts `training_required: true`, another extracts a descriptive
    string for the same key) — the same unformatted-hint failure mode the
    scorecard's property-hint watch list already names, just surfacing here
    as a hard write failure instead of a reviewable conflict, since a list
    property never reaches the conflict path (`rebuild_key`'s "lists never
    conflict" branch, above). Found live during the Phase 8 cutover: a real
    `banking.policy` union of `str`/`bool` crashed the whole rebuild
    transaction with `Neo.ClientError.Statement.TypeError`. Coerce to a
    single storable type rather than crash or silently drop a value — every
    distinct extraction survives, just as text.
    """
    out: list = []
    for value in values:
        for item in (value if _is_list(value) else [value]):
            if item not in (None, "") and item not in out:
                out.append(item)
    types = {type(item) for item in out}
    if len(types) > 1:
        logger.warning(
            "Projection: union produced mixed types {} ({!r}) — coercing to string "
            "for storage; likely an unformatted property hint",
            sorted(t.__name__ for t in types), out,
        )
        seen: set = set()
        stringified: list = []
        for item in out:
            s = str(item)
            if s not in seen:
                seen.add(s)
                stringified.append(s)
        out = stringified
    return out


def _choose_name(observations: list[dict]) -> str:
    """`name` = the longest `canonical_name`, tie-broken by frequency.

    Built from `canonical_name`, never from the verbatim `name`: raw wordings
    are preserved on the observations and unioned into `aliases`, so the
    Entity's display name is the reconciled one. Taking the longest raw name
    instead would surface exactly the measurement-laden strings the key
    function exists to strip.
    """
    names = [o.get("canonical_name") or "" for o in observations if o.get("canonical_name")]
    if not names:
        return ""
    frequency = Counter(names)
    return max(set(names), key=lambda n: (len(n), frequency[n], n))


def _conflicting_values(observations_by_valid_from: dict[str, list], prop_key: str) -> bool:
    """True when any single valid-time instant carries more than one distinct
    value -- the definition of a conflict, as opposed to history."""
    for observations in observations_by_valid_from.values():
        distinct = {_hashable(o[prop_key]) for o in observations}
        if len(distinct) > 1:
            return True
    return False


def _hashable(value):
    """Comparable form for a distinctness/variation check -- type-blind.

    A value extracted as `2` (int) in one observation and `"2"` (str) in
    another is not a genuine disagreement: `_write_conflicts()` stringifies
    every value before it ever reaches Neo4j (see below), so two values that
    render identically post-stringification must already compare equal here
    too, or a meaningless conflict gets raised over a distinction that
    disappears the moment it's stored. Coerce scalars to the same stringified
    form used at write time -- the same "coerce before comparing" policy
    `_union()` already applies to list-shaped properties, just for scalars.
    Found live: `METRIC_TARGET.value` flagged with `values=['2', '2']`. See
    neurongraph/artmind9#13.
    """
    return tuple(value) if _is_list(value) else str(value)


def merge_observations(
    observations: list[dict],
    *,
    synthesis: dict | None = None,
    override_key: tuple[str, str, str] | None = None,
) -> dict:
    """Merge one aggregate key's `latest` observations into an Entity. Pure.

    `override_key`, when given, is used as the Entity's identity instead of
    recomputing `aggregate_key` from the merged set's own chosen `name`. Only
    a same-as **merge unit** needs this: its unioned observations span more
    than one raw key by definition, so the longest-name choice among them can
    legitimately differ from the group's curated canonical -- and the
    canonical, being a human's assertion, must win over whatever the naming
    heuristic would have picked on its own. For an ordinary single-key merge
    the two always agree (every observation in the set already shares the
    same stored `key`), so passing it is harmless there too.

    Returns `{"props": {...}, "temporal_props": [...], "conflicts": [...]}`.

    Property merge is **by shape**, and there is no string concatenation
    anywhere -- the accretive `"A | B"` merge is what produced 512
    self-repeating descriptions:

    | shape                    | policy                                  |
    |--------------------------|-----------------------------------------|
    | scalar domain property   | winner (latest document `valid_from`)   |
    | `type`                   | winner                                  |
    | list domain property     | union                                   |
    | `context`                | union, capped                           |
    | `aliases`                | union of raw names + declared aliases   |
    | `name`                   | longest `canonical_name`, then frequency|
    | `description`            | synthesis when current, else winner's   |

    A scalar is never unioned: `rate_value: [3.75, 4.60, 4.50]` cannot answer
    "what is the rate?". The winner answers it, `_temporal_props` declares that
    it varies, and the observations hold the history.

    **Temporal variation and conflict are independent**, not alternatives. A
    recurrent property that takes different values at different instants goes
    into `_temporal_props`; a property with two values at the *same* instant
    raises a `:Conflict`. With three or more observations both can be true at
    once, and both are recorded.
    """
    if not observations:
        raise ValueError("merge_observations called with no observations")

    ordered = sorted(observations, key=_winner_sort_key)
    winner = ordered[-1]
    entity_class = winner.get("entity_class") or ""
    domain = winner.get("domain") or ""
    kind = winner.get("_kind") or "occurrent"
    name = _choose_name(ordered)
    key = override_key if override_key is not None else aggregate_key(name, entity_class, domain)

    props: dict = {
        "_id": entity_id(key),
        "name": name,
        "key": key_string(key),
        "entity_class": entity_class,
        "_domain": domain,
        "_kind": kind,
    }

    # ── description: synthesis when its contributing set is still current ──
    observation_ids = sorted(o.get("id") or "" for o in ordered)
    set_hash = hashlib.sha256("|".join(observation_ids).encode("utf-8")).hexdigest()
    props["_observation_set_hash"] = set_hash
    props["_observation_count"] = len(ordered)

    description, description_source, description_stale = _resolve_description(
        winner, observation_ids, set_hash, synthesis
    )
    if description:
        props["description"] = description
    props["_description_source"] = description_source
    if description_stale:
        props["_description_stale"] = True

    # ── named policies ──
    if winner.get("type"):
        props["type"] = winner["type"]

    context = _union([o.get("context") for o in ordered if o.get("context")])
    if context:
        props["context"] = context[:CONTEXT_CAP]

    aliases = _union(
        [o.get("name") for o in ordered if o.get("name")]
        + [o.get("aliases") for o in ordered if o.get("aliases")]
    )
    # Excluded by EXACT match, not by normalized key. A raw name that merely
    # shares the key -- "SmartSaver Account Tier 2 Rate - 4.70% AER (£10,001-
    # £50,000), effective 2026-01-15" -- is still a distinct thing a document
    # actually said, and dropping it would discard the most informative wording
    # in the set. Only the exact chosen name is redundant.
    aliases = [a for a in aliases if a != name]
    if aliases:
        props["aliases"] = aliases

    # ── domain properties, by shape ──
    domain_keys: list[str] = []
    for observation in ordered:
        for prop_key in observation:
            if prop_key in _OBSERVATION_SYSTEM_KEYS or prop_key in _SPECIAL_KEYS:
                continue
            if prop_key not in domain_keys:
                domain_keys.append(prop_key)

    temporal_props: list[str] = []
    conflicts: list[dict] = []

    for prop_key in domain_keys:
        contributing = [o for o in ordered if o.get(prop_key) not in (None, "", [])]
        if not contributing:
            continue
        values = [o[prop_key] for o in contributing]

        # Shape decides the policy. If ANY observation asserts a list, the
        # property is a list property and unions. A set cannot disagree with a
        # set, so lists never conflict.
        if any(_is_list(v) for v in values):
            merged = _union(values)
            if merged:
                props[prop_key] = merged
            continue

        distinct = {_hashable(v) for v in values}
        if len(distinct) > 1:
            by_valid_from: dict[str, list] = defaultdict(list)
            for observation in contributing:
                by_valid_from[observation.get("_valid_from") or ""].append(observation)

            # Temporal variation and conflict are INDEPENDENT facts, and with
            # three or more observations a property can be both. `rate_value`
            # across Jan(4.70, 5.25) / Feb(4.60) / Mar(4.50) genuinely varies
            # over time AND is disputed within January. Recording only the
            # conflict would answer "does this rate change over time?" with no.
            #
            # An earlier version made them mutually exclusive, and a single
            # bad extraction inside one document was enough to erase
            # `rate_value` from `_temporal_props` entirely.
            same_instant_disagreement = _conflicting_values(by_valid_from, prop_key)
            # Variation is measured over each instant's WINNER, not over its
            # full set of values. Comparing sets lets one bad extraction
            # manufacture history: January reading a boundary as both 10001 and
            # 10000 made `balance_min` differ from February's {10001} and land
            # in `_temporal_props`, when the range never actually changed. The
            # value AT an instant is the winner among that instant's
            # observations -- the same rule the Entity uses to answer "what is
            # it now" -- so that is what "did it change?" must compare.
            varies_across_instants = (
                len({
                    _hashable(_winner(obs_at_instant)[prop_key])
                    for obs_at_instant in by_valid_from.values()
                }) > 1
            )

            if kind == "recurrent" and varies_across_instants:
                # The thing changed. That is history, not a defect.
                temporal_props.append(prop_key)

            # occurrent (a completed event's attributes do not drift), or two
            # sources disagreeing at the same instant.
            if kind != "recurrent" or same_instant_disagreement:
                conflicts.append(
                    {
                        "property": prop_key,
                        "kind": kind,
                        "values": [
                            {
                                "value": o[prop_key],
                                "observation_id": o.get("id"),
                                "doc_id": o.get("doc_id"),
                                "valid_from": o.get("_valid_from"),
                            }
                            for o in contributing
                        ],
                    }
                )

        # The winner answers the question either way -- including under a
        # conflict, where a resolvable answer beats no answer and the
        # :Conflict node carries the disagreement.
        winning = next(
            (o[prop_key] for o in reversed(ordered) if o.get(prop_key) not in (None, "", [])),
            None,
        )
        if winning not in (None, "", []):
            props[prop_key] = winning

    if temporal_props:
        props["_temporal_props"] = sorted(temporal_props)

    # ── valid time: the window of the fact currently in force ──
    if winner.get("_valid_from"):
        props["_valid_from"] = winner["_valid_from"]
    if winner.get("_valid_to"):
        props["_valid_to"] = winner["_valid_to"]
    if winner.get("_valid_time_source"):
        props["_valid_time_source"] = winner["_valid_time_source"]

    return {
        "props": props,
        "temporal_props": sorted(temporal_props),
        "conflicts": conflicts,
        "observation_ids": observation_ids,
    }


def _resolve_description(
    winner: dict, observation_ids: list[str], set_hash: str, synthesis: dict | None
) -> tuple[str, str, bool]:
    """Pick the Entity's description, and say where it came from.

    A synthesis is a rewrite of the entity's description drawn from *all* its
    observations (Phase 6's `projection synthesize`). It survives a rebuild in
    a sibling node, so the rebuild has to decide whether it is still honest:

    - contributing set unchanged -> use it
    - set **grew** -> keep it (it is still true, just incomplete) and flag
      `_description_stale` so the next synthesize picks it up
    - set **shrank** -> discard it. Observations were retired, so the synthesis
      may assert content the corpus no longer stands behind, and asserting
      retracted content is worse than a plainer description.
    """
    fallback = winner.get("description") or ""
    if not synthesis or not synthesis.get("text"):
        return fallback, "observation", False

    if synthesis.get("observation_set_hash") == set_hash:
        return synthesis["text"], "synthesis", False

    previous = set(synthesis.get("observation_ids") or [])
    current = set(observation_ids)
    if previous and previous < current:
        return synthesis["text"], "synthesis", True
    if previous and not previous <= current:
        return fallback, "observation", False
    # No recorded id set to compare against — treat as stale rather than trust it.
    return synthesis["text"], "synthesis", True


# ── affected keys ────────────────────────────────────────────────────────────


def affected_keys(
    *,
    incoming: list[tuple[str, str, str]] | None = None,
    prior: list[tuple[str, str, str]] | None = None,
    retired: list[tuple[str, str, str]] | None = None,
    same_as_groups: list[list[tuple[str, str, str]]] | None = None,
) -> set[tuple[str, str, str]]:
    """The union of four sets. Miss any one and orphans return.

    1. keys from the **incoming** observations
    2. keys from the **prior version's** observations -- an entity renamed
       between versions leaves its old key behind, and skipping this set
       rebuilds the very orphan bug the redesign removes
    3. keys in any **same-as group** touching either set -- merging or
       unmerging changes an aggregate the incoming document never mentioned
    4. for a retire: keys from the **retired document's** observations

    Every key in the result is then rebuilt, and any key with zero `latest`
    observations has its `:Entity` deleted. That one rule replaces
    `_retire_orphaned_entities`, the `size(docIds) = 1` heuristic and the
    scoped entity GC -- all three of which failed silently, leaving 235 live
    entities sourced only by superseded documents.
    """
    keys: set[tuple[str, str, str]] = set()
    keys.update(incoming or [])
    keys.update(prior or [])
    keys.update(retired or [])

    if same_as_groups:
        for group in same_as_groups:
            members = set(group)
            if members & keys:
                keys.update(members)
    return keys


# ── the I/O half ─────────────────────────────────────────────────────────────

import re  # noqa: E402  (kept beside the I/O half it serves)

# Entity properties the rebuild must never clear. Everything else on an
# `:Entity` is projection-owned and is recomputed from observations on every
# rebuild, so a property no longer asserted by any observation disappears
# rather than lingering.
_PRESERVED_ENTITY_KEYS = ("embedding", "embedding_stale")


def _sanitize_label(value: str) -> str:
    """Mirror of `ingest._sanitize_label` — the class label shape the rest of
    the query layer already matches on. Duplicated rather than imported
    because `ingest` imports this module."""
    return re.sub(r"[^A-Za-z0-9_]", "_", (value or "").strip()).upper() or "UNKNOWN"


def read_latest_observations(tx, key: str) -> list[dict]:
    """Every `latest` observation for one aggregate key.

    `:Observation` is the label a node carries only while latest — a demoted
    one is relabelled to `:ObservationHistory` (see `ingest._retract_prior_version`
    / `lifecycle._transition`), so the label alone is the filter now; there is
    no `_status` property left to check.
    """
    rows = tx.run(
        "MATCH (o:Observation {key: $key}) RETURN properties(o) AS p",
        key=key,
    ).data()
    return [row["p"] for row in rows]


def _parse_key(key_string_value: str | None) -> tuple[str, str, str] | None:
    """`"name|class|domain"` back to the aggregate-key tuple, or `None` if it
    isn't shaped that way. Defensive: a key is always written by
    `observations.key_string`, but a stray/corrupt value should be skipped
    rather than raise mid-rebuild."""
    parts = (key_string_value or "").split("|")
    return tuple(parts) if len(parts) == 3 else None  # type: ignore[return-value]


def _delete_entity(tx, eid: str) -> None:
    """The zero-observations GC rule.

    Any key in the affected set with no `latest` observations left has its
    `:Entity` deleted outright. This single rule replaces
    `_retire_orphaned_entities`, the `size(docIds) = 1` heuristic and the
    scoped entity GC — three mechanisms that between them left 235 entities
    live whose only source was a superseded document.

    `DETACH DELETE` removes every relationship touching the node, `RELATES_TO`
    included — nothing has to separately know an aggregate edge existed.
    """
    tx.run(
        """
        MATCH (e:Entity {_id: $id})
        OPTIONAL MATCH (c:Conflict {_source: 'projection'})-[:CONFLICT_OF]->(e)
        DETACH DELETE c, e
        """,
        id=eid,
    )


def _write_conflicts(tx, eid: str, conflicts: list[dict], domain: str) -> int:
    """Replace this entity's projection conflicts.

    Scoped by `_source: 'projection'` so the pairwise adjudicator's own
    `:Conflict` nodes (`artmind.conflicts`) are untouched — they are authored
    by a different mechanism with a different id scheme and survive until
    Phase 6 retires them.
    """
    tx.run(
        """
        MATCH (c:Conflict {_source: 'projection'})-[:CONFLICT_OF]->(:Entity {_id: $id})
        DETACH DELETE c
        """,
        id=eid,
    )
    for conflict in conflicts:
        conflict_id = hashlib.sha256(f"{eid}|{conflict['property']}".encode("utf-8")).hexdigest()
        tx.run(
            """
            MATCH (e:Entity {_id: $entity_id})
            MERGE (c:Conflict {id: $id})
            SET c._source = 'projection',
                c.property = $property,
                c.entity_id = $entity_id,
                c.domain = $domain,
                c.kind = $kind,
                c.status = 'open',
                c.values = $values,
                c.detected_by = 'projection_rebuild'
            MERGE (c)-[:CONFLICT_OF]->(e)
            WITH c
            UNWIND $observation_ids AS oid
            MATCH (o:Observation {id: oid})
            MERGE (c)-[:EVIDENCE]->(o)
            """,
            id=conflict_id,
            entity_id=eid,
            property=conflict["property"],
            domain=domain,
            kind=conflict["kind"],
            # Values are rendered as strings: a conflict spans heterogeneous
            # types by definition, and Neo4j list properties must be uniform.
            values=[str(v["value"]) for v in conflict["values"]],
            observation_ids=[v["observation_id"] for v in conflict["values"] if v.get("observation_id")],
        )
    return len(conflicts)


def _relation_groups(tx, keys: list[tuple[str, str, str]]) -> tuple[list[dict], list[dict]]:
    """Every raw `ASSERTS_RELATION` edge touching any of these aggregate keys'
    `latest` observations, as two separate directional result sets.

    `keys` is a list rather than one key so a same-as **merge unit** can union
    the raw edges of every member it folds in — a relationship one document
    asserted under an alias's key must still reach the canonical entity.
    Ordinarily this is a list of one.

    Two passes rather than one bidirectional query, so an outgoing group and
    an incoming group never cross-multiply against each other in one result
    set. Both `MATCH` patterns require the **`:Observation`** label on both
    ends — an endpoint relabelled to `:ObservationHistory` (its document was
    retired, or superseded) simply stops matching, so a relationship whose
    other side has gone to history drops out of the aggregate on the next
    rebuild with no predicate anywhere having to know that happened.
    """
    outgoing: list[dict] = []
    incoming: list[dict] = []
    for key in keys:
        outgoing += tx.run(
            """
            MATCH (o:Observation {key: $key})-[r:ASSERTS_RELATION]->(t:Observation)
            RETURN r.rel_type AS rel_type, t.key AS other_key,
                   r.doc_id AS doc_id, r.chunk_id AS chunk_id
            """,
            key=key_string(key),
        ).data()
        incoming += tx.run(
            """
            MATCH (s:Observation)-[r:ASSERTS_RELATION]->(o:Observation {key: $key})
            RETURN r.rel_type AS rel_type, s.key AS other_key,
                   r.doc_id AS doc_id, r.chunk_id AS chunk_id
            """,
            key=key_string(key),
        ).data()
    return outgoing, incoming


def _group_relations(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Group raw `ASSERTS_RELATION` rows by `(rel_type, other_key)`, deduping
    and aggregating provenance. Pure — the I/O half (`_relation_groups`)
    supplies the rows.
    """
    groups: dict[tuple[str, str], dict] = {}
    for row in rows:
        other_key, rel_type = row.get("other_key"), row.get("rel_type")
        if not other_key or not rel_type:
            continue
        g = groups.setdefault((rel_type, other_key), {"chunk_ids": [], "doc_ids": [], "observation_count": 0})
        g["observation_count"] += 1
        if row.get("chunk_id") and row["chunk_id"] not in g["chunk_ids"]:
            g["chunk_ids"].append(row["chunk_id"])
        if row.get("doc_id") and row["doc_id"] not in g["doc_ids"]:
            g["doc_ids"].append(row["doc_id"])
    return groups


def _sync_relates_to(
    tx, keys: list[tuple[str, str, str]], eid: str, unit_of: dict[tuple[str, str, str], tuple[str, str, str]]
) -> int:
    """Recompute every `RELATES_TO` edge touching this entity, from scratch,
    from the raw `ASSERTS_RELATION` observation edges — the relationship
    analogue of the property rebuild above. Deduped by `(srcKey, rel_type,
    tgtKey)`, carrying `observation_count` / `chunk_ids` / `doc_ids`.

    `keys` is every raw aggregate key feeding this entity (more than one when
    it is a same-as merge unit's canonical); `unit_of` resolves an edge's
    OTHER endpoint to the entity it actually lives under today — an endpoint
    that was itself folded into some other canonical no longer has its own
    `:Entity`, so an edge to its raw key would otherwise point at nothing.
    An edge whose resolved other end turns out to BE this same entity (two
    merged aliases that asserted a relationship to each other) is a same-as
    artifact, not a real self-relationship, and is dropped.

    Both directions are independently authoritative: an edge FROM this entity
    is fully determined by this key's own outgoing `ASSERTS_RELATION` edges,
    and an edge INTO it by its incoming ones — so each can be deleted and
    recreated here without depending on the *other* endpoint's own rebuild
    having already run. Relationship endpoints can only ever be entities
    extracted in the same document (see `ingest._write_relation_observations`),
    so both keys of any edge are always in the same affected-key set; whichever
    of the two is rebuilt **second** in this pass is the one that finds the
    other endpoint's `:Entity` already `MERGE`d and actually writes the edge —
    order-independent, since either side can be "second".
    """
    outgoing_rows, incoming_rows = _relation_groups(tx, keys)
    outgoing = _group_relations(outgoing_rows)
    incoming = _group_relations(incoming_rows)

    tx.run("MATCH (e:Entity {_id: $id})-[r:RELATES_TO]->(:Entity) DELETE r", id=eid)
    tx.run("MATCH (:Entity)-[r:RELATES_TO]->(e:Entity {_id: $id}) DELETE r", id=eid)

    written = 0
    for (rel_type, other_key), agg in outgoing.items():
        parsed = _parse_key(other_key)
        if not parsed:
            continue
        tgt = entity_id(unit_of.get(parsed, parsed))
        if tgt == eid:
            continue
        tx.run(
            """
            MATCH (e:Entity {_id: $src})
            MATCH (t:Entity {_id: $tgt})
            MERGE (e)-[r:RELATES_TO {rel_type: $rel_type}]->(t)
            SET r.observation_count = $observation_count, r.chunk_ids = $chunk_ids, r.doc_ids = $doc_ids
            """,
            src=eid, tgt=tgt, rel_type=rel_type, **agg,
        )
        written += 1
    for (rel_type, other_key), agg in incoming.items():
        parsed = _parse_key(other_key)
        if not parsed:
            continue
        src = entity_id(unit_of.get(parsed, parsed))
        if src == eid:
            continue
        tx.run(
            """
            MATCH (s:Entity {_id: $src})
            MATCH (e:Entity {_id: $tgt})
            MERGE (s)-[r:RELATES_TO {rel_type: $rel_type}]->(e)
            SET r.observation_count = $observation_count, r.chunk_ids = $chunk_ids, r.doc_ids = $doc_ids
            """,
            src=src, tgt=eid, rel_type=rel_type, **agg,
        )
        written += 1
    return written


def rebuild_key(
    tx,
    key: tuple[str, str, str],
    *,
    member_keys: list[tuple[str, str, str]] | None = None,
    unit_of: dict[tuple[str, str, str], tuple[str, str, str]] | None = None,
    synthesis: dict | None = None,
) -> str:
    """Rebuild one aggregate key. Returns `rebuilt`, `deleted`, or `absent`.

    `member_keys` is every raw key whose `latest` observations feed this
    entity — more than one only when `key` is a same-as merge unit's
    canonical (see `rebuild`'s group planning); otherwise just `[key]`.
    `unit_of` resolves a `RELATES_TO` edge's other endpoint the same way, for
    entities merged away by the same groups.

    Runs inside the caller's transaction — a raise here fails the commit that
    dirtied the projection, by design.
    """
    eid = entity_id(key)
    member_keys = member_keys or [key]
    unit_of = unit_of or {}
    observations: list[dict] = []
    for member_key in member_keys:
        observations.extend(read_latest_observations(tx, key_string(member_key)))

    if not observations:
        existing = tx.run("MATCH (e:Entity {_id: $id}) RETURN count(e) AS c", id=eid).single()
        if existing and existing["c"]:
            _delete_entity(tx, eid)
            return "deleted"
        return "absent"

    merged = merge_observations(observations, synthesis=synthesis, override_key=key)
    props = merged["props"]
    label = _sanitize_label(props.get("entity_class") or "")
    keep = list(props.keys()) + list(_PRESERVED_ENTITY_KEYS)

    # MERGE on the deterministic id — never delete-and-recreate. Recreating
    # churns `elementId` and breaks every external reference to the node.
    #
    # `apoc.create.removeProperties` clears what this rebuild no longer
    # asserts, while `keep` protects `embedding` and `embedding_stale`. The
    # embedding is LEFT IN PLACE and merely flagged when the description
    # changes: the rebuild is inside a transaction and cannot call the embed
    # service, and a null embedding is absent from `entity_embedding`, which
    # would make the entity invisible to `entity-resolve`'s vector leg rather
    # than merely less accurate. Stale still finds the entity; null deletes it.
    tx.run(
        f"""
        MERGE (e:Entity {{_id: $id}})
        ON CREATE SET e.embedding_stale = true
        WITH e, e.description AS prior_description
        CALL apoc.create.addLabels(e, $labels) YIELD node
        WITH node, prior_description
        CALL apoc.create.removeProperties(node, [k IN keys(node) WHERE NOT k IN $keep]) YIELD node AS cleaned
        SET cleaned += $props
        SET cleaned.embedding_stale = CASE
              WHEN cleaned.embedding IS NULL THEN true
              WHEN prior_description IS NULL AND $description IS NULL THEN coalesce(cleaned.embedding_stale, false)
              WHEN prior_description IS NULL OR prior_description <> $description THEN true
              ELSE coalesce(cleaned.embedding_stale, false)
            END
        RETURN cleaned
        """.replace("$labels", f"['{label}']"),
        id=eid,
        keep=keep,
        props=props,
        description=props.get("description"),
    )

    # Rewire provenance: the Entity aggregates exactly its current observations.
    tx.run(
        """
        MATCH (e:Entity {_id: $id})
        OPTIONAL MATCH (e)-[r:AGGREGATES]->(:Observation)
        DELETE r
        WITH e
        UNWIND $observation_ids AS oid
        MATCH (o:Observation {id: oid})
        MERGE (e)-[:AGGREGATES]->(o)
        """,
        id=eid,
        observation_ids=merged["observation_ids"],
    )

    _write_conflicts(tx, eid, merged["conflicts"], props.get("_domain") or "")
    _sync_relates_to(tx, member_keys, eid, unit_of)
    return "rebuilt"


# ── same-as groups: merge within (class, domain), link across it ───────────


def _plan_groups(
    keys, groups: list[list[tuple[str, str, str]]]
) -> tuple[
    dict[tuple[str, str, str], tuple[str, str, str]],
    dict[tuple[str, str, str], list[tuple[str, str, str]]],
    list[tuple[tuple[str, str, str], tuple[str, str, str]]],
]:
    """Pure. Turn the groups touching `keys` into a rebuild plan.

    A group's members are split against its own canonical (`group[0]`, see
    `same_as.py`) — never against each other, so the group stays a bounded,
    canonical-centric assertion rather than a transitive closure:

    - same `(entity_class, domain)` as the canonical -> **merge**: folded into
      one Entity, written at `entity_id(canonical)`.
    - different class or domain -> **link**: keeps its own Entity, joined to
      the canonical's by a `SAME_AS` edge. `_domain` stays scalar on both —
      merging across domains would force it into a list, and Neo4j can't
      index-back an `any(...)` predicate over a list property, which is what
      every domain-scoped query in the system relies on.

    Returns `(unit_of, members_of, links)`:
      `unit_of[key]`     -- the key whose Entity `key`'s observations feed.
                             Present only for merge units; a key mapping to
                             itself IS the merge unit's canonical.
      `members_of[key]`  -- for a merge canonical, every raw key unioned in
                             (itself included).
      `links`            -- `(member_key, canonical_key)` pairs to `SAME_AS`.

    Relies on the caller's `keys` already containing every member of any
    group it touches — `affected_keys`'s set 3 guarantees this for every
    normal (document/chat-triggered) rebuild; a rebuild seeded any other way
    should pass the full key population (e.g. via `full_rebuild`).
    """
    in_scope = set(keys)
    unit_of: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    members_of: dict[tuple[str, str, str], list[tuple[str, str, str]]] = {}
    links: list[tuple[tuple[str, str, str], tuple[str, str, str]]] = []

    for group in groups:
        if not (in_scope & set(group)):
            continue
        canonical = group[0]
        merge_members = [canonical]
        for member in group[1:]:
            if member[1] == canonical[1] and member[2] == canonical[2]:
                merge_members.append(member)
            else:
                links.append((member, canonical))
        if len(merge_members) > 1:
            for member in merge_members:
                unit_of[member] = canonical
            members_of[canonical] = merge_members

    return unit_of, members_of, links


def _sync_same_as(tx, member_key: tuple[str, str, str], canonical_key: tuple[str, str, str]) -> None:
    """`MERGE` a `SAME_AS` edge, both directions, between a link member's own
    Entity and its group's canonical Entity. A no-op, safely, if either does
    not (yet) exist — the `MATCH` simply finds nothing.

    Callers clear stale `SAME_AS` edges before re-syncing (see `rebuild`), the
    same delete-then-recreate idiom `_sync_relates_to` uses, so removing a
    group from `same_as.yaml` cleanly drops the edge on the next rebuild.
    """
    tx.run(
        """
        MATCH (m:Entity {_id: $m}), (c:Entity {_id: $c})
        MERGE (m)-[:SAME_AS]->(c)
        MERGE (c)-[:SAME_AS]->(m)
        """,
        m=entity_id(member_key), c=entity_id(canonical_key),
    )


def apply_retractions(tx, observations: list[dict]) -> set[tuple[str, str, str]]:
    """Apply every `_retracts` pointer among `observations`, before the keys
    they touch are rebuilt.

    A retracting observation names an existing `Observation.id` or
    `ASSERTS_RELATION.id` it declares no longer true (see
    `observations.build_observation`'s `retracts` parameter). Declarative:
    the retracting observation is written like any other, by the caller,
    before this runs; nothing here mutates it.

    - Retracting an **Observation** demotes it to `:ObservationHistory` — the
      same label swap `lifecycle._transition` uses for a whole document, here
      scoped to one fact. It is never deleted, only removed from the `latest`
      pool that feeds the aggregate.
    - Retracting a **relationship** deletes the `ASSERTS_RELATION` edge
      outright. Edges carry no history label, and `RELATES_TO` is already
      recomputed from scratch on every rebuild (`_sync_relates_to`), so
      deleting the raw edge is sufficient — the aggregate just stops
      asserting it on the next sync.

    Tolerant by design: a `_retracts` pointer matching nothing (already
    retracted, already retired, a typo) is logged and skipped rather than
    failing the commit.

    Returns the aggregate keys the retracted **targets** belonged to — these
    can differ from the retracting observation's own key (a chat may retract
    a fact about a different entity than the one it is adding), so the caller
    must union this into the affected-key set or the target's aggregate goes
    unrebuilt.
    """
    keys: set[tuple[str, str, str]] = set()
    for observation in observations:
        target = observation.get("_retracts")
        if not target:
            continue
        rec = tx.run(
            """
            MATCH (o:Observation {id: $id})
            REMOVE o:Observation SET o:ObservationHistory
            RETURN o.key AS key
            """,
            id=target,
        ).single()
        if rec:
            parsed = _parse_key(rec["key"])
            if parsed:
                keys.add(parsed)
            logger.info("Retracted observation {} -> :ObservationHistory", target)
            continue

        edge_exists = tx.run(
            "MATCH ()-[r:ASSERTS_RELATION {id: $id}]->() RETURN count(r) AS n", id=target
        ).single()
        if edge_exists and edge_exists["n"]:
            tx.run("MATCH ()-[r:ASSERTS_RELATION {id: $id}]->() DELETE r", id=target)
            logger.info("Retracted relationship {} (ASSERTS_RELATION deleted)", target)
            continue

        logger.warning(
            "retraction target {!r} matched no Observation or ASSERTS_RELATION edge; skipped",
            target,
        )
    return keys


def rebuild(tx, keys, *, same_as_groups: list[list[tuple[str, str, str]]] | None = None, synthesis_loader=None) -> dict:
    """Rebuild the given aggregate keys inside the caller's transaction.

    `same_as_groups` defaults to `same_as.load_groups()` — pass an explicit
    (possibly filtered) list only when the caller has one in hand already.
    `synthesis_loader(key) -> dict | None` is the synthesize seam; absent,
    every description falls back to the winner observation's.
    """
    if same_as_groups is None:
        from artmind import same_as

        same_as_groups = same_as.load_groups()

    unit_of, members_of, links = _plan_groups(keys, same_as_groups)

    # Clear stale SAME_AS edges up front, for every key that keeps its own
    # Entity this pass (a folded-away key's edges vanish with it via
    # _delete_entity's DETACH DELETE, below). Re-synced from `links` after.
    for key in keys:
        canonical = unit_of.get(key)
        if canonical is not None and canonical != key:
            continue
        tx.run(
            "MATCH (e:Entity {_id: $id}) OPTIONAL MATCH (e)-[r:SAME_AS]-(:Entity) DELETE r",
            id=entity_id(canonical if canonical is not None else key),
        )

    summary = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0}
    for key in sorted(keys):
        summary["keys"] += 1
        canonical = unit_of.get(key)
        if canonical is not None and canonical != key:
            # Folded into another entity by a same-as group — never its own.
            _delete_entity(tx, entity_id(key))
            summary["deleted"] += 1
            continue
        effective_key = canonical if canonical is not None else key
        member_keys = members_of.get(effective_key, [effective_key])
        synthesis = synthesis_loader(effective_key) if synthesis_loader else None
        outcome = rebuild_key(tx, effective_key, member_keys=member_keys, unit_of=unit_of, synthesis=synthesis)
        summary[outcome] += 1

    for member_key, canonical_key in links:
        _sync_same_as(tx, member_key, canonical_key)

    logger.info(
        "Projection rebuild: {} key(s) — rebuilt={} deleted={} absent={}",
        summary["keys"], summary["rebuilt"], summary["deleted"], summary["absent"],
    )
    return summary


def all_keys(tx, domains: list[str] | None = None) -> set[tuple[str, str, str]]:
    """Every aggregate key with at least one `latest` observation, plus every
    key an existing `:Entity` claims — so a full rebuild also sweeps entities
    whose observations have all gone.

    `:Observation` already means latest (a demoted node carries
    `:ObservationHistory` instead — see `read_latest_observations`), so there
    is no status clause left to add here; only the domain-property NAME
    differs between the two labels (`Entity._domain` vs. `Observation.domain`).
    """
    keys: set[tuple[str, str, str]] = set()
    params: dict = {}
    domain_clause = {"Observation": "", "Entity": ""}
    if domains:
        params["domains"] = domains
        domain_clause["Observation"] = (
            " AND (n.domain IN $domains OR any(d IN $domains WHERE n.domain STARTS WITH d + '.'))"
        )
        domain_clause["Entity"] = (
            " AND (n._domain IN $domains OR any(d IN $domains WHERE n._domain STARTS WITH d + '.'))"
        )

    for label in ("Observation", "Entity"):
        rows = tx.run(
            f"MATCH (n:{label}) WHERE n.key IS NOT NULL{domain_clause[label]} "
            "RETURN DISTINCT n.key AS key",
            **params,
        ).data()
        for row in rows:
            parsed = _parse_key(row["key"])
            if parsed:
                keys.add(parsed)
    return keys


def full_rebuild(tx, domains: list[str] | None = None, *, synthesis_loader=None) -> dict:
    """Rebuild every key. The deferred path for a directory ingest, and the
    recovery path for drift a hand-edited `same_as.yaml` or a schema change
    introduced.

    `domains=None` (every domain) is also the only shape that updates
    `:ProjectionState` — same_as.yaml and the schema set are both global, so a
    partial, one-domain-family full_rebuild can't honestly claim the whole
    projection has caught up with them.
    """
    from artmind import same_as

    groups = same_as.load_groups()
    keys = all_keys(tx, domains)
    logger.info("Full projection rebuild over {} key(s)", len(keys))
    summary = rebuild(tx, keys, same_as_groups=groups, synthesis_loader=synthesis_loader)
    if domains is None:
        record_rebuild(tx, same_as_hash=same_as.content_hash(), schema_hash=schema_set_hash())
    return summary


def keys_for_document(tx, doc_id: str, *, status: str | None = None) -> set[tuple[str, str, str]]:
    """The aggregate keys a document's observations contribute to.

    `status` selects by **label**, not by a property — there is none left:
    `"latest"` matches only `:Observation`, `"history"` only
    `:ObservationHistory`, and `None` (the default) matches either, which is
    what set 2 of `affected_keys` needs — the *prior* version's keys are
    exactly the ones a rename would otherwise strand, whichever pool they're
    currently in.
    """
    if status == "latest":
        cypher = (
            "MATCH (o:Observation {doc_id: $doc_id}) WHERE o.key IS NOT NULL "
            "RETURN DISTINCT o.key AS key"
        )
    elif status == "history":
        cypher = (
            "MATCH (o:ObservationHistory {doc_id: $doc_id}) WHERE o.key IS NOT NULL "
            "RETURN DISTINCT o.key AS key"
        )
    else:
        cypher = (
            "MATCH (o) WHERE (o:Observation OR o:ObservationHistory) "
            "AND o.doc_id = $doc_id AND o.key IS NOT NULL "
            "RETURN DISTINCT o.key AS key"
        )
    rows = tx.run(cypher, doc_id=doc_id).data()
    keys: set[tuple[str, str, str]] = set()
    for row in rows:
        parsed = _parse_key(row["key"])
        if parsed:
            keys.add(parsed)
    return keys


# ── synthesize seam ──────────────────────────────────────────────────────────


def load_synthesis(tx, key: tuple[str, str, str]) -> dict | None:
    """The real `synthesis_loader`: read the `:Synthesis` sibling node for
    this key's entity, if one exists. Every `rebuild`/`full_rebuild` call site
    should pass this (or a closure wrapping it) so a synthesis actually
    survives a rebuild — see `docs/projection-pipeline.md` §3.

    The `:Synthesis` node is keyed on the entity's own deterministic id, not
    on the key string, so it is untouched by a same-as group forming or
    dissolving around a *different* key mapping to the same canonical.
    """
    rec = tx.run(
        "MATCH (s:Synthesis {id: $id}) RETURN properties(s) AS p", id=entity_id(key)
    ).single()
    return rec.get("p") if rec else None


# ── :ProjectionState — drift detection ──────────────────────────────────────

_PROJECTION_STATE_ID = "singleton"


def schema_set_hash(domains_dir=None) -> str:
    """Hash of every domain schema file's content, sorted by filename.
    Detects any schema edit (a `kind` flip, a new domain, a property change)
    without needing to interpret *what* changed — same shape as
    `same_as.content_hash`."""
    from pathlib import Path

    from paths import DOMAIN_SCHEMAS_DIR

    target_dir = Path(domains_dir) if domains_dir else DOMAIN_SCHEMAS_DIR
    if not target_dir.exists():
        return ""
    hasher = hashlib.sha256()
    for f in sorted(target_dir.glob("*.yaml")):
        hasher.update(f.name.encode("utf-8"))
        hasher.update(f.read_bytes())
    return hasher.hexdigest()


def record_rebuild(tx, *, same_as_hash: str, schema_hash: str) -> None:
    """Update the `:ProjectionState` singleton after a FULL (all-domain)
    rebuild. Scoped/incremental rebuilds never call this — drift is about
    whether a full rebuild has caught up with the curation/schema files, and
    an incremental rebuild proves nothing about the keys it didn't touch."""
    tx.run(
        """
        MERGE (s:ProjectionState {id: $id})
        SET s.same_as_hash = $same_as_hash,
            s.schema_hash = $schema_hash,
            s.last_rebuilt_at = $now
        """,
        id=_PROJECTION_STATE_ID,
        same_as_hash=same_as_hash,
        schema_hash=schema_hash,
        now=datetime.now(timezone.utc).isoformat(),
    )


def read_state(tx) -> dict | None:
    rec = tx.run(
        "MATCH (s:ProjectionState {id: $id}) RETURN properties(s) AS p", id=_PROJECTION_STATE_ID
    ).single()
    return rec.get("p") if rec else None


def status(tx) -> dict:
    """Compare the recorded `:ProjectionState` against `same_as.yaml` and the
    schema set right now. Read-only, deliberately: queries run through
    `read_session()` (`READ_ACCESS`), so drift is reported, never auto-fixed —
    that guarantee is worth more than the convenience of a query silently
    triggering a write."""
    from artmind import same_as

    current_same_as = same_as.content_hash()
    current_schema = schema_set_hash()
    recorded = read_state(tx)
    if not recorded:
        return {
            "known": False,
            "same_as_drift": True,
            "schema_drift": True,
            "current_same_as_hash": current_same_as,
            "current_schema_hash": current_schema,
            "unembedded_chunks": tx.run(
                "MATCH (c:DocChunk) WHERE c.embedding IS NULL RETURN count(c) AS n"
            ).single()["n"],
        }
    return {
        "known": True,
        "last_rebuilt_at": recorded.get("last_rebuilt_at"),
        "same_as_drift": recorded.get("same_as_hash") != current_same_as,
        "schema_drift": recorded.get("schema_hash") != current_schema,
        "recorded_same_as_hash": recorded.get("same_as_hash"),
        "current_same_as_hash": current_same_as,
        "recorded_schema_hash": recorded.get("schema_hash"),
        "current_schema_hash": current_schema,
        "unembedded_chunks": tx.run(
            "MATCH (c:DocChunk) WHERE c.embedding IS NULL RETURN count(c) AS n"
        ).single()["n"],
    }
