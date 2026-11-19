"""A fact's dimension members: edges, then declared properties, then the same chunk (spec 2026-11-19). Pure.

For each fact and each dimension (person, place), members are collected from three sources, in order:

1. a `RELATES_TO` edge from the fact to a member (role: the edge's `rel_type`, as read);
2. the fact class's declared properties (`from_properties`), each value matched to a member by name or alias,
   with the person dimension's collectives; a name shared by two members links nobody (role `property:<name>`);
3. only when 1 and 2 gave the fact no member of that dimension, and the dimension opted in with `same_chunk`:
   the members extracted from the fact's own chunks (role `same_chunk`).

A member found by an earlier source is not repeated by a later one.
"""
from __future__ import annotations

import re

#: A person's name needs this many characters to match by text (`_people` has always used 4); a place's name is
#: matched whole, so a short one ("Goa") is fine.
MIN_PERSON_NAME = 4
MIN_PLACE_NAME = 3


def _norm(value) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _index(members: list[dict], min_len: int) -> dict[str, set]:
    """Normalised name or alias -> member keys."""
    names: dict[str, set] = {}
    for m in members or []:
        props = m.get("props") or {}
        for n in [props.get("name"), *(props.get("aliases") or [])]:
            n = _norm(n)
            if len(n) >= min_len:
                names.setdefault(n, set()).add(m["key"])
    return names


def _collective_groups(collectives: dict, names: dict[str, set]) -> dict[str, set]:
    groups: dict[str, set] = {}
    for phrase, members in (collectives or {}).items():
        keys: set = set()
        for m in members:
            k = names.get(_norm(m))
            if k and len(k) == 1:
                keys |= k
        if keys:
            groups[_norm(phrase)] = keys
    return groups


def _values(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v is not None and str(v).strip()]
    return [str(value)]


def _split(text: str) -> list[str]:
    """A property value may hold several names ("Lake Inn, Hill Town"); the whole text is tried first."""
    parts = [text]
    if re.search(r"[,;/&]|\band\b", text):
        parts += [p for p in re.split(r"\s*(?:,|;|/|&|\band\b)\s*", text) if p.strip()]
    return parts


def _from_property(values: list[str], names: dict[str, set], groups: dict[str, set]) -> list[str]:
    found: list[str] = []
    for text in values:
        for part in _split(text):
            key = _norm(part)
            keys = names.get(key)
            if keys and len(keys) == 1:  # an ambiguous name links nobody
                found.extend(keys)
            for phrase, gkeys in groups.items():
                if re.search(rf"\b{re.escape(phrase)}\b", key):
                    found.extend(sorted(gkeys))
    return list(dict.fromkeys(found))


def link_all(facts: list[dict], edges: list[dict], same_chunk: dict[str, list[dict]], person, place,
             members: dict[str, list[dict]]) -> list[dict]:
    """Bridge rows `{fact_key, member_key, member_class, role}` for every fact and both dimensions.

    `facts`: `{key, class, dim_props: {property name: value}}`. `edges`: `graph.bridges()` rows.
    `same_chunk`: per dimension class, `{fact_key, member_key}` rows from the fact's chunks.
    `person` / `place`: the declaration's dimensions (`from_properties`, `same_chunk`, `collectives`).
    `members`: per dimension class, `graph.members()` rows.
    """
    out: list[dict] = []
    dims = ((person, MIN_PERSON_NAME), (place, MIN_PLACE_NAME))
    seen: set[tuple] = set()

    def add(fact_key, member_key, member_class, role):
        k = (fact_key, member_key)
        if k not in seen:
            seen.add(k)
            out.append({"fact_key": fact_key, "member_key": member_key, "member_class": member_class, "role": role})

    for e in edges:
        if e.get("member_class") in (person.entity_class, place.entity_class):
            add(e["fact_key"], e["member_key"], e["member_class"], e.get("role"))
    edged = {(b["fact_key"], b["member_class"]) for b in out}
    for dim, min_len in dims:
        names = _index(members.get(dim.entity_class) or [], min_len)
        groups = _collective_groups(getattr(dim, "collectives", {}), names) if dim is person else {}
        chunk_rows: dict[str, list[str]] = {}
        if dim.same_chunk:
            for r in same_chunk.get(dim.entity_class) or []:
                chunk_rows.setdefault(r["fact_key"], []).append(r["member_key"])
        for f in facts:
            props = (f.get("dim_props") or {})
            had = False
            for prop in (dim.from_properties.get(f.get("class")) or []):
                for mk in _from_property(_values(props.get(prop)), names, groups):
                    add(f["key"], mk, dim.entity_class, f"property:{prop}")
                    had = True
            if dim.same_chunk and not had and (f["key"], dim.entity_class) not in edged:
                for mk in dict.fromkeys(chunk_rows.get(f["key"], [])):
                    add(f["key"], mk, dim.entity_class, "same_chunk")
    return out
