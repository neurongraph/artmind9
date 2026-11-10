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


def _day_states(facts):
    """Per date: 'away' (away evidence, no home), 'home', 'mixed' (both: a travel day), or None."""
    seen: dict[str, set] = {}
    for f in facts:
        if f.get("date") and f.get("home_away") in ("home", "away"):
            seen.setdefault(f["date"], set()).add(f["home_away"])
    return {d: ("mixed" if len(s) == 2 else next(iter(s))) for d, s in seen.items()}


def _trips(w, tables):
    """Trips at day level (spec §7): a run of consecutive days that are away (or a travel day
    at either end), plus days between them with listed-family facts and no home evidence;
    at least one fully away day. Members are the run's listed-family and away facts."""
    facts = [f for f in tables["fact_happening"] if f.get("date")]
    state = _day_states(facts)
    fam_days = {f["date"] for f in facts if f.get("type_family") in w.families}
    candidate = sorted(d for d in set(state) | fam_days if state.get(d) in ("away", "mixed")
                       or (state.get(d) is None and d in fam_days))
    runs: list[list[str]] = []
    for d in candidate:
        if runs and (_d(d) - _d(runs[-1][-1])).days <= w.consecutive_days:
            runs[-1].append(d)
        else:
            runs.append([d])
    groups = []
    for run in runs:
        while run and state.get(run[0]) is None:      # a trip starts and ends on known days
            run = run[1:]
        while run and state.get(run[-1]) is None:
            run = run[:-1]
        if not any(state.get(d) == "away" for d in run):
            continue
        days = set(run)
        members = sorted((f for f in facts if f["date"] in days and (
            f.get("type_family") in w.families or f.get("home_away") == "away")), key=lambda f: f["date"])
        if members:
            groups.append(members)
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
