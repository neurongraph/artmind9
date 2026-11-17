"""`ingest date-audit`: how a domain's staged facts are dated, per class (spec 2026-11-15 §3.4).

Read-only: staging only, no registry, Neo4j or LLM. Counts and shares, never values.
"""
from __future__ import annotations

import json
import shlex
from collections import Counter
from pathlib import Path

from artmind.retime import staging_folders
from artmind.temporal import _entity_temporal_mapping, load_schema, parse_valid


def audit_folders(folders: list[Path], schema: dict) -> list[dict]:
    """One row per class with a `temporal:` property, from the folders' `observations.json`:
    `observations`, `dated_by_property` and its `share`, the `precision` mix and `ranges`
    among those, and `unparsed` (tagged values present that hold no date). An unreadable
    file is skipped."""
    mapping = _entity_temporal_mapping(schema)
    stats: dict[str, dict] = {}
    for folder in folders:
        path = folder / "observations.json"
        try:
            observations = json.loads(path.read_text(encoding="utf-8")) or []
        except (OSError, ValueError):
            continue
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            cls = obs.get("entity_class") or ""
            props = mapping.get(cls)
            if not props:
                continue
            s = stats.setdefault(cls, {"observations": 0, "dated": 0, "precision": Counter(),
                                       "ranges": 0, "unparsed": 0})
            s["observations"] += 1
            if obs.get("_valid_time_source") == "property":
                s["dated"] += 1
                s["precision"][obs.get("_valid_precision") or "unknown"] += 1
                s["ranges"] += 1 if obs.get("_valid_to") else 0
            s["unparsed"] += sum(1 for p in props.values()
                                 if obs.get(p) not in (None, "") and parse_valid(obs.get(p)) is None)
    return [
        {"class": cls, "observations": s["observations"], "dated_by_property": s["dated"],
         "share": round(s["dated"] / s["observations"], 3) if s["observations"] else 0.0,
         "precision": dict(sorted(s["precision"].items())), "ranges": s["ranges"], "unparsed": s["unparsed"]}
        for cls, s in sorted(stats.items())
    ]


def audit_domain(domain: str) -> dict:
    """The `ingest date-audit` payload. `next` is the retime dry run when a value is unparsed."""
    folders = staging_folders(domain)
    rows = audit_folders(folders, load_schema(domain))
    return {
        "next": f"artmind ingest retime --domain {shlex.quote(domain)} --dryRun"
        if any(r["unparsed"] for r in rows) else None,
        "domain": domain, "documents": len(folders),
        "rows": rows, "rows_total": len(rows), "truncated": False,
    }
