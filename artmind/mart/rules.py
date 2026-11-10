"""Pure deciders for dimension members (spec §6.2 steps 2-3). Each returns the level
values it can decide, or None when it cannot; no I/O, no LLM."""
from __future__ import annotations

import re

_LEVELS = ("group", "relation", "side")


def _full(d: dict) -> dict:
    return {k: d.get(k) for k in _LEVELS}


def decide_person(text, word_list: dict) -> dict | None:
    t = str(text or "").strip().lower()
    if not t:
        return None
    patterns = sorted((word_list.get("patterns") or {}).items(), key=lambda kv: -len(kv[0]))
    for pattern, levels in patterns:
        if pattern.startswith("-") and pattern in t:
            return _full(levels)
        if re.search(rf"\b{re.escape(pattern)}\b", t):
            return _full(levels)
    exact = word_list.get("exact") or {}
    if t in exact:
        return _full(exact[t])
    for word in sorted(exact, key=len, reverse=True):
        if re.search(rf"\b{re.escape(word)}\b", t):
            return _full(exact[word])
    return None


def decide_type(value, type_dim) -> dict | None:
    family = type_dim.family_of(value)
    return {"family": family} if family else None


def decide_place(props: dict, seed_property: str | None) -> dict | None:
    name = props.get("name")
    if str(props.get("place_type") or props.get("type") or "").lower() == "city":
        return {"city": name}
    parent = props.get(seed_property) if seed_property else None
    if isinstance(parent, list):
        parent = parent[0] if parent else None
    if parent:
        return {"venue": name, "city": str(parent)}
    return None


def home_away(place_levels: dict, home_city: str | None) -> str | None:
    city = (place_levels or {}).get("city")
    if not city:
        return None
    if not home_city:
        return "away"
    return "home" if city.strip().lower() == home_city.strip().lower() else "away"


def chain_places(seeded: dict, names: dict) -> dict:
    """Second seed pass: a place whose parent `city` is itself a place with a parent city of
    its own is in a locality of that city (venue -> locality -> city)."""
    by_name = {str(n).strip().lower(): k for k, n in names.items() if n}
    out = dict(seeded)
    for key, levels in seeded.items():
        if not levels or "venue" not in levels:
            continue
        parent_key = by_name.get(str(levels.get("city") or "").strip().lower())
        parent = seeded.get(parent_key) if parent_key and parent_key != key else None
        if parent and parent.get("venue") and parent.get("city") and \
                parent["city"].strip().lower() != str(levels["city"]).strip().lower():
            out[key] = {"venue": levels["venue"], "locality": levels["city"], "city": parent["city"]}
    return out
