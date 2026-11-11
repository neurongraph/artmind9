"""Dimension enrichment (spec §6.2): user records, then rules, then graph seeds, then the
LLM for what is left (Task 8). Writes curation records; dry run plans only."""
from __future__ import annotations

from artmind.mart import graph as mart_graph
from artmind.mart import records, rules, words


def _undecided(existing, dimension, key):
    """Rules and seeds are recomputed on every run (deterministic); user and LLM records stay."""
    rec = existing.get((dimension, key))
    return rec is None or rec.get("source") in ("rule", "seed")


def enrich(decl, domains, *, dry_run: bool, use_llm: bool, llm=None, limit: int | None = None,
           dimensions: tuple | list | None = None) -> dict:
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
    places = mart_graph.members(domains, decl.place.entity_class)
    cities = {str((m["props"] or {}).get("name")).strip().lower() for m in places
              if str((m["props"] or {}).get("place_type") or "").lower() == "city" and (m["props"] or {}).get("name")}
    if decl.home_city:
        cities.add(decl.home_city.strip().lower())
    seeded = {m["key"]: rules.decide_place(m["props"] or {}, decl.place.seed_property, cities) for m in places}
    seeded = rules.chain_places(seeded, {m["key"]: (m["props"] or {}).get("name") for m in places})
    for m in places:
        if not _undecided(existing, "place", m["key"]):
            continue
        levels = seeded.get(m["key"])
        if levels and levels.get("city"):
            note("place", "seed")
            if not dry_run:
                records.put_dimension(decl.domain, "place", m["key"], levels, source="seed", confidence=1.0)
        else:  # no city from the graph: the LLM decides, seeing the locality text in the member's props
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
    asked = [p for p in pending if not dimensions or p["dimension"] in dimensions]
    asked = asked if limit is None else asked[:limit]
    llm_calls = len(asked) if use_llm else 0
    result = {"domain": decl.domain, "decided": decided, "undecided": undecided, "llm_calls": llm_calls}
    if use_llm and not dry_run and llm is not None:
        result["llm"] = llm(decl, asked)
    return result
