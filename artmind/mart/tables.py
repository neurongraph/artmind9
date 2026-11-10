"""Pure assembly of every mart table from graph reads and records (spec §7)."""
from __future__ import annotations

from datetime import date as _date

from artmind.mart import rules

PLACE_LEVELS = ("venue", "locality", "city", "region", "country")
PERSON_LEVELS = ("group", "relation", "side")


def _day(value) -> str | None:
    s = str(value or "")[:10]
    try:
        _date.fromisoformat(s)
        return s
    except ValueError:
        return None


def _time_row(day: str, periods: list[dict]) -> dict:
    d = _date.fromisoformat(day)
    iso = d.isocalendar()
    names = [p["name"] for p in periods if p["start"] <= day <= p["end"]]
    return {"date": day, "week": f"{iso[0]}-W{iso[1]:02d}", "month": day[:7], "quarter": f"{d.year}-Q{(d.month - 1) // 3 + 1}",
            "year": str(d.year), "period": names[0] if names else None}


def assemble(decl, happenings, mentions, bridges, recs, periods, *, members) -> dict[str, list[dict]]:
    place_of: dict[str, str] = {}
    bridge_person, bridge_place = [], []
    for b in bridges:
        role = str(b.get("role") or "").lower()
        if b["member_class"] == decl.person.entity_class:
            bridge_person.append({"entity_key": b["fact_key"], "person_key": b["member_key"], "role": role})
        elif b["member_class"] == decl.place.entity_class:
            bridge_place.append({"entity_key": b["fact_key"], "place_key": b["member_key"], "role": role})
            place_of.setdefault(b["fact_key"], b["member_key"])

    def place_levels(key):
        return (recs.get(("place", key)) or {}).get("levels") or {}

    def family(value):
        rec = recs.get(("type", f"type:{str(value or '').lower()}"))
        return (rec or {}).get("levels", {}).get("family") or decl.type.family_of(value)

    fact_happening = []
    for h in happenings:
        pk = place_of.get(h["key"])
        fact_happening.append({
            "entity_key": h["key"], "name": h.get("name"), "class": h.get("class"), "date": _day(h.get("date")),
            "type": h.get("type"), "type_family": family(h.get("type")), "place_key": pk,
            "home_away": rules.home_away(place_levels(pk), decl.home_city) if pk else None,
            "outcome": h.get("outcome"), "first_mention": _day(h.get("first")), "last_mention": _day(h.get("last")),
            "mention_count": len(h.get("obs") or []), "observation_ids": list(h.get("obs") or []),
            "chunk_ids": list(h.get("chunks") or []), "doc_ids": list(h.get("docs") or [])})
    fact_mention = []
    for m in mentions:
        pk = place_of.get(m["entity_key"])
        fact_mention.append({
            "observation_id": m["id"], "entity_key": m["entity_key"], "class": m.get("class"),
            "date": _day(m.get("date")), "type": m.get("type"), "type_family": family(m.get("type")),
            "place_key": pk, "home_away": rules.home_away(place_levels(pk), decl.home_city) if pk else None,
            "doc_id": m.get("doc_id"), "chunk_id": m.get("chunk_id"), "sentence": m.get("sentence")})
    dim_person = [{"person_key": p["key"], "name": (p.get("props") or {}).get("name"),
                   **{lv: ((recs.get(("person", p["key"])) or {}).get("levels") or {}).get(lv) for lv in PERSON_LEVELS}}
                  for p in members.get(decl.person.entity_class, [])]
    dim_place = []
    for p in members.get(decl.place.entity_class, []):
        lv = place_levels(p["key"])
        dim_place.append({"place_key": p["key"], "name": (p.get("props") or {}).get("name"),
                          **{k: lv.get(k) for k in PLACE_LEVELS},
                          "home_away": rules.home_away(lv, decl.home_city)})
    days = sorted({r["date"] for r in fact_happening + fact_mention if r["date"]})
    dim_time = [_time_row(d, periods) for d in days]
    types = sorted({str(r["type"]) for r in fact_happening + fact_mention if r.get("type")})
    dim_type = [{"type": t, "family": family(t)} for t in types]
    return {"fact_happening": fact_happening, "fact_mention": fact_mention, "bridge_person": bridge_person,
            "bridge_place": bridge_place, "dim_person": dim_person, "dim_place": dim_place, "dim_time": dim_time,
            "dim_type": dim_type}
