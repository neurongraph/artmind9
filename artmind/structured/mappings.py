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


def _values_match(value: str, names: set[str], *, fuzzy_threshold: float) -> bool:
    value_lower = value.lower()
    if value_lower in {n.lower() for n in names}:
        return True
    return any(
        difflib.SequenceMatcher(None, value_lower, name.lower()).ratio() >= fuzzy_threshold
        for name in names
    )


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

    Returns the list of persisted proposals (``{"column", "entity_class",
    "confidence"}``); columns with no class above ``CONFIDENCE_FLOOR`` are
    omitted entirely (nothing persisted for them).
    """
    names_by_class = _entity_names_by_class(domains)
    if not names_by_class:
        return []

    proposals: list[dict] = []
    for column in registry.get_columns(table_id):
        if column.get("profile_json") is None:
            continue
        profile = json.loads(column["profile_json"])
        if profile.get("kind") != "categorical":
            continue
        sample = profile.get("distinct_sample") or []
        if not sample:
            continue

        best_class = None
        best_confidence = 0.0
        for entity_class, names in names_by_class.items():
            matched = sum(
                1 for value in sample if _values_match(str(value), names, fuzzy_threshold=fuzzy_threshold)
            )
            confidence = matched / len(sample)
            if confidence > best_confidence:
                best_confidence = confidence
                best_class = entity_class

        if best_class is not None and best_confidence >= CONFIDENCE_FLOOR:
            proposals.append(
                {
                    "column": column["name"],
                    "entity_class": best_class,
                    "confidence": best_confidence,
                }
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
