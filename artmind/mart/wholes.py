"""Wholes by rule (spec §7): trips and life events over assembled facts. Pure."""
from __future__ import annotations

import hashlib
from datetime import date


def _d(s):
    return date.fromisoformat(s)


def _wid(kind, key):
    return hashlib.sha256(f"{kind}|{key}".encode()).hexdigest()[:16]


def _people(tables):
    by_fact: dict[str, set] = {}
    for b in tables.get("bridge_person") or []:
        by_fact.setdefault(b["entity_key"], set()).add(b["person_key"])
    return by_fact


def _trips(w, tables):
    groups_of = {p["person_key"]: p.get("group") for p in tables.get("dim_person") or []}
    never = set(w.alone_never_joins) | ({"self"} if "narrator" in w.alone_never_joins else set())
    frequent = {k for k, g in groups_of.items() if g in never}
    people = _people(tables)
    facts = sorted((f for f in tables["fact_happening"] if f.get("date") and f.get("type_family") in w.families
                    and (w.place != "away" or f.get("home_away") == "away")), key=lambda f: f["date"])
    groups: list[list[dict]] = []
    for f in facts:
        joined = False
        for g in reversed(groups):
            last = g[-1]
            if (_d(f["date"]) - _d(last["date"])).days > w.consecutive_days:
                break
            shared_people = (people.get(f["entity_key"], set()) & set().union(*(people.get(x["entity_key"], set()) for x in g))) - frequent
            shared_place = "place" in w.share and f.get("place_key") and f["place_key"] in {x.get("place_key") for x in g}
            if ("participant" in w.share and shared_people) or shared_place:
                g.append(f)
                joined = True
                break
        if not joined:
            groups.append([f])
    return groups


def _life_events(w, tables):
    people = _people(tables)
    dims = {p["person_key"]: p for p in tables.get("dim_person") or []}
    out = []
    for pk, person in dims.items():
        name = str(person.get("name") or "").lower()
        mine = [f for f in tables["fact_happening"] if f.get("date") and f.get("type_family") in w.families and (
            ("edge" in w.match and pk in people.get(f["entity_key"], set()))
            or ("name" in w.match and name and name in str(f.get("name") or "").lower()))]
        mine.sort(key=lambda f: f["date"])
        group: list[dict] = []
        for f in mine:
            if group and (_d(f["date"]) - _d(group[-1]["date"])).days > (w.window_gap_days or 0):
                out.append(group)
                group = []
            group.append(f)
        if group:
            out.append(group)
    return out


def compute(decl, tables) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {"fact_whole": []}
    for kind, w in decl.wholes.items():
        groups = _trips(w, tables) if w.anchor is None else _life_events(w, tables)
        rows = []
        for g in groups:
            wid = _wid(kind, g[0]["entity_key"])
            start, end = g[0]["date"], g[-1]["date"]
            rows.append({"whole_id": wid, "start": start, "end": end, "days": (_d(end) - _d(start)).days + 1,
                         "member_count": len(g), "place_keys": sorted({f["place_key"] for f in g if f.get("place_key")})})
            last_health = max((f["date"] for f in g if f.get("type_family") == "health"), default=None)
            for i, f in enumerate(g):
                if kind == "life_event" or w.anchor:
                    role = "aftermath" if last_health and f["date"] > last_health and f.get("type_family") != "health" else "during"
                else:
                    role = "start" if i == 0 else ("end" if i == len(g) - 1 else "during")
                result["fact_whole"].append({"kind": kind, "whole_id": wid, "entity_key": f["entity_key"], "role": role})
        result[f"whole_{kind}"] = rows
    return result
