"""`ingest date-audit`: how a domain's staged facts are dated, per class (spec 2026-11-15 §3.4).

Read-only: staging only, no registry, Neo4j or LLM. Counts and shares, never values. A family
root (`banking`) is audited with its children: staging under `kg/banking/` and `kg/banking.*/`,
each folder read with the schema of the domain it was extracted under.
"""
from __future__ import annotations

import json
import shlex
from collections import Counter
from pathlib import Path
from typing import Callable

from artmind.temporal import _entity_temporal_mapping, load_schema, parse_valid


def staging_folders(domain: str) -> list[Path]:
    """The document staging folders of `domain` and its dotted children (as property-audit)."""
    from artmind import ingest
    from artmind.property_audit import _folders

    return _folders(ingest.KG_DIR, domain)


def audit_folders(folders: list[Path], schema: dict | Callable[[str], dict]) -> list[dict]:
    """One row per class with a `temporal:` property, from the folders' `observations.json`:
    `observations`, `dated_by_property` and its `share`, the `precision` mix and `ranges`
    among those, and `unparsed` (tagged values present that hold no date). `schema` is one
    schema, or a function from a folder's domain (its parent folder's name) to its schema.
    An unreadable file is skipped.

    A range is an end the fact's own value gave (`ingest._fact_valid_time`), counted as
    `ingest`'s per-document `date_counts` count it; a `_valid_to` the fact took from its
    document (spec 2026-11-15 §7.2: facts gain their document's end where they have none)
    is not one.
    """
    from artmind.ingest import _fact_valid_time

    mappings: dict[str, dict] = {}

    def mapping_for(folder: Path) -> dict:
        if not callable(schema):
            return mappings.setdefault("", _entity_temporal_mapping(schema))
        name = folder.parent.name
        if name not in mappings:
            mappings[name] = _entity_temporal_mapping(schema(name) or {})
        return mappings[name]

    stats: dict[str, dict] = {}
    for folder in folders:
        try:
            observations = json.loads((folder / "observations.json").read_text(encoding="utf-8")) or []
        except (OSError, ValueError):
            continue
        if not isinstance(observations, list):
            continue
        mapping = mapping_for(folder)
        for obs in observations:
            if not isinstance(obs, dict):
                continue
            cls = obs.get("entity_class") or ""
            if not mapping.get(cls):
                continue
            fact = _fact_valid_time(mapping, cls, obs)
            s = stats.setdefault(cls, {"observations": 0, "dated": 0, "precision": Counter(),
                                       "ranges": 0, "unparsed": 0})
            s["observations"] += 1
            if obs.get("_valid_time_source") == "property":
                s["dated"] += 1
                s["precision"][obs.get("_valid_precision") or "unknown"] += 1
                s["ranges"] += 1 if fact.valid_from and fact.valid_to else 0
            s["unparsed"] += fact.unparsed
    return [
        {"class": cls, "observations": s["observations"], "dated_by_property": s["dated"],
         "share": round(s["dated"] / s["observations"], 3) if s["observations"] else 0.0,
         "precision": dict(sorted(s["precision"].items())), "ranges": s["ranges"], "unparsed": s["unparsed"]}
        for cls, s in sorted(stats.items())
    ]


def _has_schema(domain: str) -> bool:
    from artmind.schema_validate import family_schema

    return bool(load_schema(domain) or (family_schema(domain).get("entity_types")))


def audit_domain(domain: str) -> dict:
    """The `ingest date-audit` payload. `next` is the retime dry run when a value is unparsed;
    a domain with neither a schema nor staged documents is named in `warnings`."""
    folders = staging_folders(domain)
    rows = audit_folders(folders, load_schema)
    warnings = [] if folders or _has_schema(domain) else [
        f"domain {domain!r} has no schema and no staged documents; nothing to audit"
    ]
    return {
        "warnings": warnings,
        "next": f"artmind ingest retime --domain {shlex.quote(domain)} --dryRun"
        if any(r["unparsed"] for r in rows) else None,
        "domain": domain, "documents": len(folders),
        "rows": rows, "rows_total": len(rows), "truncated": False,
    }
