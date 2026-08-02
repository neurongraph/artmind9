"""Resolve a free-text phrase to a canonical column value and/or graph entity.

Matches ``phrase`` against a structured-store column's profiled distinct
values (registry ``profile_json``, Task 2.1) and/or knowledge-graph entity
names (``graph_query.entity_listing``), scoped to the same domain(s). Order
per candidate: exact (case-folded) -> fuzzy (``difflib``) -> embedding (a
documented later upgrade, not v1 — it would need live Neo4j vector search and
break hermetic testing).

Deterministic string matching is the right tool *here*, unlike in mapping
proposal (``structured/semantics.py``), which retired its difflib matcher for
an LLM call: this resolves a phrase the user actually typed against values that
actually exist, which is a lexical question. Judging whether a column *denotes*
a schema class is a semantic one.

Query-time only: no anchor nodes are persisted (spec §3 Level-2) — this
mirrors ``text2sql``'s "no writes" posture.
"""

import difflib
import json

from artmind.graph_query import entity_listing, normalize_domains
from artmind.structured import registry

# Below this SequenceMatcher ratio a fuzzy match is not surfaced at all, even
# as a low-confidence candidate. Deliberately looser than a bulk-sample
# threshold would be: a single phrase-vs-name comparison has no "fraction of
# samples matched" signal to firm up the confidence the way scoring a column's
# whole distinct_sample does.
MATCH_THRESHOLD = 0.6


def _score(phrase_lower: str, value) -> float:
    value_lower = str(value).lower()
    if value_lower == phrase_lower:
        return 1.0
    return difflib.SequenceMatcher(None, phrase_lower, value_lower).ratio()


def _entity_names_by_class(domains: list[str]) -> dict[str, set[str]]:
    """Flatten ``entity_listing``'s rows into ``{entity_class: {names}}``."""
    listing = entity_listing(domains)
    names_by_class: dict[str, set[str]] = {}
    for row in listing.get("rows", []):
        entity_class = row.get("label", "")
        class_names = names_by_class.setdefault(entity_class, set())
        for type_group in row.get("typeGroups", []):
            class_names.update(type_group.get("names", []))
    return names_by_class


def _rank(phrase_lower: str, entries: list[tuple[str, str | None]], source: str) -> list[dict]:
    """Score+sort ``(value, entity_class)`` pairs against ``phrase_lower``,
    tagging each with ``source`` and dropping anything below MATCH_THRESHOLD."""
    scored = [
        {
            "value": value,
            "entity_class": entity_class,
            "score": _score(phrase_lower, value),
            "source": source,
        }
        for value, entity_class in entries
    ]
    scored = [c for c in scored if c["score"] >= MATCH_THRESHOLD]
    scored.sort(key=lambda c: c["score"], reverse=True)
    return scored


def _merge_candidates(graph_ranked: list[dict], column_ranked: list[dict]) -> list[dict]:
    """Union ``graph_ranked`` and ``column_ranked``, collapsing a graph entry and
    a column entry into one ``source: "both"`` candidate when they name the same
    value (case-insensitive) — mirrors the case-insensitive equality check that
    already decides the top-level ``source``, so the two never disagree about
    whether a given value was a "both" match."""
    column_by_lower = {c["value"].lower(): c for c in column_ranked}
    merged: list[dict] = []
    matched_column_lower: set[str] = set()

    for g in graph_ranked:
        key = g["value"].lower()
        col_match = column_by_lower.get(key)
        if col_match is not None:
            merged.append(
                {
                    "value": g["value"],
                    "entity_class": g["entity_class"],
                    "score": max(g["score"], col_match["score"]),
                    "source": "both",
                }
            )
            matched_column_lower.add(key)
        else:
            merged.append(g)

    merged.extend(c for c in column_ranked if c["value"].lower() not in matched_column_lower)

    merged.sort(key=lambda c: c["score"], reverse=True)
    return merged


def _find_table_id(domains: list[str], table: str | None, column: str) -> int | None:
    """Resolve a ``table_id`` for ``column``, scoped to ``domains``.

    If ``table`` is given, look it up directly in each domain (first hit
    wins). Otherwise scan every registered table in ``domains`` for one whose
    columns include ``column`` by exact name (first hit wins) — if the same
    column name exists in multiple tables across the domain set, the first
    one returned by ``registry.list_tables`` (ordered by ``table_name``) is
    used; disambiguate with ``--table``/``table=`` when that matters.
    """
    if table:
        for domain in domains:
            row = registry.get_table(table, domain=domain)
            if row:
                return row["id"]
        return None

    for row in registry.list_tables(domains):
        for col in registry.get_columns(row["id"]):
            if col["name"] == column:
                return row["id"]
    return None


def _column_sample(domains: list[str], table: str | None, column: str) -> list:
    """The profiled ``distinct_sample`` for ``column``, or ``[]`` if the
    table/column isn't found or the column isn't categorical (numeric/other
    columns have no meaningful discrete sample to match a phrase against)."""
    table_id = _find_table_id(domains, table, column)
    if table_id is None:
        return []
    for col in registry.get_columns(table_id):
        if col["name"] != column:
            continue
        if col.get("profile_json") is None:
            return []
        profile = json.loads(col["profile_json"])
        if profile.get("kind") != "categorical":
            return []
        return profile.get("distinct_sample") or []
    return []


def resolve_key(
    phrase: str,
    domains,
    *,
    column: str | None = None,
    table: str | None = None,
    top_k: int = 5,
) -> dict:
    """Resolve ``phrase`` to a canonical column value and/or graph entity name.

    Always matches against graph entity names (``entity_listing``, scoped to
    ``domains``). If ``column`` is given, also matches against that column's
    profiled ``distinct_sample`` values (optionally scoped to ``table`` when
    the column name is ambiguous across tables); without ``column`` the graph
    is the only source consulted.

    Returns ``{phrase, canonical, source, column, entity_class, candidates}``:

    - ``source`` is ``"both"`` when the best column match and the best graph
      match agree (case-insensitively) on the same value; otherwise it is
      whichever side produced the higher-scoring match (the graph wins exact
      ties, since it names a canonical KG entity rather than raw column
      data); or ``None`` if neither side found anything at/above
      ``MATCH_THRESHOLD``.
    - ``entity_class`` is populated only when the graph side contributed to
      the winning ``canonical`` (source ``"graph"`` or ``"both"``); ``None``
      otherwise — a column-only match has no known entity class.
    - ``candidates`` is the ranked union of both sides' matches (score
      descending, ties broken by insertion order) above ``MATCH_THRESHOLD``,
      capped at ``top_k`` — a value matched on both sides collapses into a
      single ``source: "both"`` entry rather than appearing twice. Empty when
      nothing matched — an unknown phrase resolves to
      ``canonical: None, source: None, candidates: []``.

    Raises ``ValueError`` if ``top_k < 1`` (mirroring ``normalize_domains``'s
    validation, caught the same way by the CLI) — a non-positive cap would
    otherwise silently empty ``candidates`` even when ``canonical``/``source``
    are populated, contradicting the "empty only when nothing matched"
    invariant above.
    """
    if top_k < 1:
        raise ValueError(f"top_k must be >= 1, got {top_k}")

    domains = normalize_domains(domains)
    phrase_lower = phrase.lower()

    names_by_class = _entity_names_by_class(domains)
    graph_entries = [
        (name, entity_class) for entity_class, names in names_by_class.items() for name in names
    ]
    graph_ranked = _rank(phrase_lower, graph_entries, "graph")

    column_ranked: list[dict] = []
    if column:
        sample = _column_sample(domains, table, column)
        column_entries = [(str(value), None) for value in sample]
        column_ranked = _rank(phrase_lower, column_entries, "column")

    best_graph = graph_ranked[0] if graph_ranked else None
    best_column = column_ranked[0] if column_ranked else None

    canonical = None
    source = None
    entity_class = None

    if best_graph and best_column:
        if best_graph["value"].lower() == best_column["value"].lower():
            canonical, source, entity_class = best_graph["value"], "both", best_graph["entity_class"]
        elif best_column["score"] > best_graph["score"]:
            canonical, source = best_column["value"], "column"
        else:
            canonical, source, entity_class = best_graph["value"], "graph", best_graph["entity_class"]
    elif best_graph:
        canonical, source, entity_class = best_graph["value"], "graph", best_graph["entity_class"]
    elif best_column:
        canonical, source = best_column["value"], "column"

    candidates = _merge_candidates(graph_ranked, column_ranked)[:top_k]

    return {
        "phrase": phrase,
        "canonical": canonical,
        "source": source,
        "column": column,
        "entity_class": entity_class,
        "candidates": candidates,
    }
