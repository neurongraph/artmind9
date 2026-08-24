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
        "_status",
        "_kind",
        "_valid_from",
        "_valid_to",
        "_doc_valid_from",
        "_valid_time_source",
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
    because the observations feeding it are sorted."""
    out: list = []
    for value in values:
        for item in (value if _is_list(value) else [value]):
            if item not in (None, "") and item not in out:
                out.append(item)
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


def _conflicting_values(values_by_valid_from: dict[str, list]) -> bool:
    """True when any single valid-time instant carries more than one distinct
    value -- the definition of a conflict, as opposed to history."""
    for values in values_by_valid_from.values():
        distinct = {_hashable(v) for v in values}
        if len(distinct) > 1:
            return True
    return False


def _hashable(value):
    return tuple(value) if _is_list(value) else value


def merge_observations(
    observations: list[dict],
    *,
    synthesis: dict | None = None,
) -> dict:
    """Merge one aggregate key's `latest` observations into an Entity. Pure.

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
    """
    if not observations:
        raise ValueError("merge_observations called with no observations")

    ordered = sorted(observations, key=_winner_sort_key)
    winner = ordered[-1]
    entity_class = winner.get("entity_class") or ""
    domain = winner.get("domain") or ""
    kind = winner.get("_kind") or "occurrent"
    name = _choose_name(ordered)
    key = aggregate_key(name, entity_class, domain)

    props: dict = {
        "id": entity_id(key),
        "name": name,
        "key": key_string(key),
        "entity_class": entity_class,
        "domain": domain,
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
    aliases = [a for a in aliases if normalize_name(a) != normalize_name(name)]
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
                by_valid_from[observation.get("_valid_from") or ""].append(observation[prop_key])

            same_instant_disagreement = _conflicting_values(by_valid_from)
            if kind == "recurrent" and not same_instant_disagreement:
                # The thing changed. That is history, not a defect.
                temporal_props.append(prop_key)
            else:
                # occurrent (a completed event's attributes do not drift), or
                # two sources disagreeing at the same instant.
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
    """Every `latest` observation for one aggregate key."""
    rows = tx.run(
        "MATCH (o:Observation {key: $key}) WHERE o._status = 'latest' RETURN properties(o) AS p",
        key=key,
    ).data()
    return [row["p"] for row in rows]


def _delete_entity(tx, eid: str) -> None:
    """The zero-observations GC rule.

    Any key in the affected set with no `latest` observations left has its
    `:Entity` deleted outright. This single rule replaces
    `_retire_orphaned_entities`, the `size(docIds) = 1` heuristic and the
    scoped entity GC — three mechanisms that between them left 235 entities
    live whose only source was a superseded document.
    """
    tx.run(
        """
        MATCH (e:Entity {id: $id})
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
        MATCH (c:Conflict {_source: 'projection'})-[:CONFLICT_OF]->(:Entity {id: $id})
        DETACH DELETE c
        """,
        id=eid,
    )
    for conflict in conflicts:
        conflict_id = hashlib.sha256(f"{eid}|{conflict['property']}".encode("utf-8")).hexdigest()
        tx.run(
            """
            MATCH (e:Entity {id: $entity_id})
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


def rebuild_key(tx, key: tuple[str, str, str], *, synthesis: dict | None = None) -> str:
    """Rebuild one aggregate key. Returns `rebuilt`, `deleted`, or `absent`.

    Runs inside the caller's transaction — a raise here fails the commit that
    dirtied the projection, by design.
    """
    eid = entity_id(key)
    observations = read_latest_observations(tx, key_string(key))

    if not observations:
        existing = tx.run("MATCH (e:Entity {id: $id}) RETURN count(e) AS c", id=eid).single()
        if existing and existing["c"]:
            _delete_entity(tx, eid)
            return "deleted"
        return "absent"

    merged = merge_observations(observations, synthesis=synthesis)
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
        MERGE (e:Entity {{id: $id}})
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
        MATCH (e:Entity {id: $id})
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

    _write_conflicts(tx, eid, merged["conflicts"], props.get("domain") or "")
    return "rebuilt"


def rebuild(tx, keys, *, synthesis_loader=None) -> dict:
    """Rebuild the given aggregate keys inside the caller's transaction.

    `synthesis_loader(key) -> dict | None` is the Phase 6 seam; absent, every
    description falls back to the winner observation's.
    """
    summary = {"rebuilt": 0, "deleted": 0, "absent": 0, "keys": 0}
    for key in sorted(keys):
        synthesis = synthesis_loader(key) if synthesis_loader else None
        outcome = rebuild_key(tx, key, synthesis=synthesis)
        summary[outcome] += 1
        summary["keys"] += 1
    logger.info(
        "Projection rebuild: {} key(s) — rebuilt={} deleted={} absent={}",
        summary["keys"], summary["rebuilt"], summary["deleted"], summary["absent"],
    )
    return summary


def all_keys(tx, domains: list[str] | None = None) -> set[tuple[str, str, str]]:
    """Every aggregate key with at least one `latest` observation, plus every
    key an existing `:Entity` claims — so a full rebuild also sweeps entities
    whose observations have all gone."""
    keys: set[tuple[str, str, str]] = set()
    domain_filter = ""
    params: dict = {}
    if domains:
        domain_filter = " AND (n.domain IN $domains OR any(d IN $domains WHERE n.domain STARTS WITH d + '.'))"
        params["domains"] = domains

    for label, status in (("Observation", " AND n._status = 'latest'"), ("Entity", "")):
        rows = tx.run(
            f"MATCH (n:{label}) WHERE n.key IS NOT NULL{status}{domain_filter} "
            "RETURN DISTINCT n.key AS key",
            **params,
        ).data()
        for row in rows:
            parts = (row["key"] or "").split("|")
            if len(parts) == 3:
                keys.add(tuple(parts))
    return keys


def full_rebuild(tx, domains: list[str] | None = None, *, synthesis_loader=None) -> dict:
    """Rebuild every key. The deferred path for a directory ingest, and the
    recovery path for drift a hand-edited `same_as.yaml` or a schema change
    introduced."""
    keys = all_keys(tx, domains)
    logger.info("Full projection rebuild over {} key(s)", len(keys))
    return rebuild(tx, keys, synthesis_loader=synthesis_loader)


def keys_for_document(tx, doc_id: str, *, status: str | None = None) -> set[tuple[str, str, str]]:
    """The aggregate keys a document's observations contribute to.

    With `status=None` this spans every version, which is what set 2 of
    `affected_keys` needs: the *prior* version's keys are exactly the ones a
    rename would otherwise strand.
    """
    clause = " AND o._status = $status" if status else ""
    params = {"doc_id": doc_id}
    if status:
        params["status"] = status
    rows = tx.run(
        f"MATCH (o:Observation {{doc_id: $doc_id}}) WHERE o.key IS NOT NULL{clause} "
        "RETURN DISTINCT o.key AS key",
        **params,
    ).data()
    keys: set[tuple[str, str, str]] = set()
    for row in rows:
        parts = (row["key"] or "").split("|")
        if len(parts) == 3:
            keys.add(tuple(parts))
    return keys
