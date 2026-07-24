"""Propose ``column -> entity_class`` mappings by matching categorical column
samples against domain KG entity names.

Exact (case-folded) matches are tried first, falling back to ``difflib``
fuzzy matching (mirroring ``artmind/conflicts.py``'s ``_name_ratio``) — stdlib,
hermetic; embedding-based matching is a documented later upgrade, not v1.
"""

import difflib
import json

from artmind.graph_query import entity_listing
from artmind.structured import registry

# Confidence floor below which a proposal is not persisted (plan's example).
CONFIDENCE_FLOOR = 0.4


def _lowered(names: set[str]) -> tuple[set[str], list[str]]:
    """Case-fold ``names`` once: a set for exact membership, a list for fuzzy scan."""
    lowered_list = [n.lower() for n in names]
    return set(lowered_list), lowered_list


def _value_matches(
    value_lower: str, lowered_set: set[str], lowered_list: list[str], *, fuzzy_threshold: float
) -> bool:
    if value_lower in lowered_set:
        return True
    return any(
        difflib.SequenceMatcher(None, value_lower, name).ratio() >= fuzzy_threshold
        for name in lowered_list
    )


def _candidate_columns(table_id: int) -> list[tuple[str, list]]:
    """Categorical columns with a non-empty sample, as ``[(name, distinct_sample)]``.

    Cheap and local (SQLite only) — callers use this to decide whether the
    ``entity_listing`` graph round trip is even worth making.
    """
    candidates = []
    for column in registry.get_columns(table_id):
        if column.get("profile_json") is None:
            continue
        profile = json.loads(column["profile_json"])
        if profile.get("kind") != "categorical":
            continue
        sample = profile.get("distinct_sample") or []
        if not sample:
            continue
        candidates.append((column["name"], sample))
    return candidates


def _entity_names_by_class(domains: list[str]) -> dict[str, set[str]]:
    listing = entity_listing(domains)
    names_by_class: dict[str, set[str]] = {}
    for row in listing.get("rows", []):
        entity_class = row["label"]
        class_names = names_by_class.setdefault(entity_class, set())
        for type_group in row.get("typeGroups", []):
            class_names.update(type_group.get("names", []))
    return names_by_class


def propose_mappings(
    table_id: int, domains: list[str], *, fuzzy_threshold: float = 0.82
) -> list[dict]:
    """Propose and persist ``column -> entity_class`` mappings for ``table_id``'s
    categorical columns, matched against ``domains``' KG entities.

    A confirmed mapping (set via the review CLI, Task 2.3) is never
    overwritten by a re-proposal — refreshing a table must not silently
    un-confirm a user-reviewed mapping.

    Returns the list of persisted proposals (``{"column", "entity_class",
    "confidence"}``); columns with no class above ``CONFIDENCE_FLOOR``, and
    columns whose best class matches an already-confirmed mapping, are
    omitted (nothing persisted for either).
    """
    # Cheap local SQLite check first — only pay for the entity_listing graph
    # round trip if the table actually has a categorical column to match.
    candidate_columns = _candidate_columns(table_id)
    if not candidate_columns:
        return []

    names_by_class = _entity_names_by_class(domains)
    if not names_by_class:
        return []

    # Case-fold each class's names once, not once per sampled value.
    lowered_by_class = {
        entity_class: _lowered(names) for entity_class, names in names_by_class.items()
    }

    already_confirmed = {
        (mapping["column"], mapping["entity_class"])
        for mapping in registry.list_mappings(table_id)
        if mapping.get("confirmed")
    }

    proposals: list[dict] = []
    for column_name, sample in candidate_columns:
        best_class = None
        best_confidence = 0.0
        for entity_class, (lowered_set, lowered_list) in lowered_by_class.items():
            matched = sum(
                1
                for value in sample
                if _value_matches(
                    str(value).lower(), lowered_set, lowered_list, fuzzy_threshold=fuzzy_threshold
                )
            )
            confidence = matched / len(sample)
            if confidence > best_confidence:
                best_confidence = confidence
                best_class = entity_class

        if best_class is None or best_confidence < CONFIDENCE_FLOOR:
            continue
        if (column_name, best_class) in already_confirmed:
            continue  # leave a user-confirmed mapping untouched
        proposals.append(
            {"column": column_name, "entity_class": best_class, "confidence": best_confidence}
        )

    for proposal in proposals:
        registry.upsert_mapping(
            table_id,
            proposal["column"],
            proposal["entity_class"],
            proposal["confidence"],
            confirmed=False,
        )

    return proposals
