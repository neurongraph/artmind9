"""`mart status` (spec §8): enrichment coverage, unreviewed proposals."""
from __future__ import annotations

from artmind.mart import graph as mart_graph
from artmind.mart import records


def _counts(decl, domains) -> dict[str, int]:
    return {"person": len(mart_graph.members(domains, decl.person.entity_class)),
            "place": len(mart_graph.members(domains, decl.place.entity_class))}


def report(decl, domains) -> dict:
    recs = records.load_dimensions(decl.domain)
    counts = _counts(decl, domains)
    cov = {}
    for dim, keys in (("place", ("country", "city")), ("person", ("group",))):
        mine = [r for (d, _), r in recs.items() if d == dim]
        entry = {"members": counts.get(dim, 0)}
        for k in keys:
            entry[f"with_{k}"] = sum(1 for r in mine if (r.get("levels") or {}).get(k))
        by_source: dict[str, int] = {}
        for r in mine:
            by_source[r["source"]] = by_source.get(r["source"], 0) + 1
        entry["by_source"] = by_source
        cov[dim] = entry
    return {"domain": decl.domain, "coverage": cov, "proposed": len(records.proposed(decl.domain))}
