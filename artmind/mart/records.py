"""Mart curation records (spec §6.3): one JSON file per decision, the durable source of
truth; mart tables are derived from them. Kinds: `mart_dimension`, `mart_calendar`."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from artmind import curation_records

DIMENSION_KIND = "mart_dimension"
CALENDAR_KIND = "mart_calendar"
_RANK = {"user": 3, "rule": 2, "seed": 2, "llm": 1}


def _id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:24]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def put_dimension(domain, dimension, member_key, levels, *, source, confidence, model=None,
                  status="applied") -> str:
    rid = _id(domain, dimension, member_key, source)
    curation_records.write_record(DIMENSION_KIND, {
        "id": rid, "domain": domain, "dimension": dimension, "member_key": member_key, "levels": levels,
        "source": source, "confidence": confidence, "model": model, "status": status, "decided_at": _now()})
    return rid


def _all(kind: str) -> list[dict]:
    directory = curation_records.curation_dir() / kind
    if not directory.is_dir():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("*.json"))]


def load_dimensions(domain, *, canonical: dict | None = None, include_proposed: bool = False) -> dict:
    """{(dimension, member_key): record}, the highest-ranked source per member; member keys
    are mapped through `canonical` (same-as merges)."""
    canonical = canonical or {}
    best: dict = {}
    for r in _all(DIMENSION_KIND):
        if r.get("domain") != domain or (r.get("status") == "proposed" and not include_proposed):
            continue
        key = (r["dimension"], canonical.get(r["member_key"], r["member_key"]))
        cur = best.get(key)
        if cur is None or _RANK.get(r["source"], 0) > _RANK.get(cur["source"], 0):
            best[key] = r
    return best


def proposed(domain) -> list[dict]:
    return [r for r in _all(DIMENSION_KIND) if r.get("domain") == domain and r.get("status") == "proposed"]


def put_period(domain, name, start, end, *, source) -> str:
    rid = _id(domain, name, start)
    curation_records.write_record(CALENDAR_KIND, {"id": rid, "domain": domain, "name": name, "start": start,
                                                  "end": end, "source": source, "decided_at": _now()})
    return rid


def load_periods(domain) -> list[dict]:
    return [r for r in _all(CALENDAR_KIND) if r.get("domain") == domain]


def periods_on(domain, day: str) -> list[str]:
    return [p["name"] for p in load_periods(domain) if p["start"] <= day <= p["end"]]
