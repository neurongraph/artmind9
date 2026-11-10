"""Dimension enrichment (spec §6.2): user records, then rules, then graph seeds, then the
LLM for what is left (Task 8). Writes curation records; dry run plans only."""
from __future__ import annotations

from artmind.mart import graph as mart_graph
from artmind.mart import records, rules, words


def _undecided(existing, dimension, key):
    return (dimension, key) not in existing


def enrich(decl, domains, *, dry_run: bool, use_llm: bool, llm=None, limit: int | None = None) -> dict:
    existing = records.load_dimensions(decl.domain, include_proposed=True)
    word_list = words.load(decl.person.words or "kinship")
    decided: dict[str, dict[str, int]] = {}
    pending: list[dict] = []

    def note(dim, source):
        decided.setdefault(dim, {}).setdefault(source, 0)
        decided[dim][source] += 1

    for m in mart_graph.members(domains, decl.person.entity_class):
        if not _undecided(existing, "person", m["key"]):
            continue
        levels = rules.decide_person((m["props"] or {}).get(decl.person.seed_property), word_list)
        if levels:
            note("person", "rule")
            if not dry_run:
                records.put_dimension(decl.domain, "person", m["key"], levels, source="rule", confidence=1.0)
        else:
            pending.append({"dimension": "person", **m})
    for m in mart_graph.members(domains, decl.place.entity_class):
        if not _undecided(existing, "place", m["key"]):
            continue
        levels = rules.decide_place(m["props"] or {}, decl.place.seed_property)
        if levels:
            note("place", "seed")
            if not dry_run:
                records.put_dimension(decl.domain, "place", m["key"], levels, source="seed", confidence=1.0)
        else:
            pending.append({"dimension": "place", **m})
    seen_types = sorted({str(h.get("type")) for h in mart_graph.happenings(domains, decl.type.classes)
                         if h.get("type")})
    for value in seen_types:
        key = f"type:{value.lower()}"
        if not _undecided(existing, "type", key):
            continue
        levels = rules.decide_type(value, decl.type)
        if levels:
            note("type", "rule")
            if not dry_run:
                records.put_dimension(decl.domain, "type", key, levels, source="rule", confidence=1.0)
        else:
            pending.append({"dimension": "type", "key": key, "props": {"name": value}})
    undecided: dict[str, int] = {}
    for p in pending:
        undecided[p["dimension"]] = undecided.get(p["dimension"], 0) + 1
    llm_calls = len(pending if limit is None else pending[:limit]) if use_llm else 0
    result = {"domain": decl.domain, "decided": decided, "undecided": undecided, "llm_calls": llm_calls}
    if use_llm and not dry_run and llm is not None:
        result["llm"] = llm(decl, pending if limit is None else pending[:limit])
    return result
