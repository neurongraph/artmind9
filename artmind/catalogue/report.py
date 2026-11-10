"""What `artmind query catalogue` and `artmind catalogue refresh` return (spec 2026-11-02 §7).

Pure shaping over `cache`: no command-line parsing, no output. `refresh` writes the
catalogue caches (memory and `logs/plan-catalog/`) and nothing else.
"""
from __future__ import annotations

from artmind.catalogue import cache, parts as parts_mod
from artmind.catalogue.model import PART_FIELDS

#: Parts that are built from the graph, so they carry the generation they were built at.
GRAPH_PARTS = frozenset({"classes", "relations", "documents", "names", "time", "assets", "levels",
                         "shapes", "types", "versions"})


def resolve_parts(parts) -> tuple:
    """`parts` checked against the available builders; none given means every available part."""
    parts = tuple(parts or ())
    return cache._check_parts(parts) if parts else cache.available_parts()


def _part_data(cat, part: str, names: bool):
    as_json = cat.to_json()
    fields = PART_FIELDS[part]
    if part == "names" and not names:
        return {"count": len(cat.names), "truncated": cat.truncated}
    values = {f: as_json[f] for f in fields}
    return values[fields[0]] if len(values) == 1 else values


def _generation(cat, part: str):
    key = cat.part_keys.get(part)
    return key[0] if part in GRAPH_PARTS and key else None


def catalogue_payload(domains, parts, names: bool = False) -> dict:
    """Per domain, per part: its data, where it came from (`memory|disk|live|absent`), the
    generation it was built at and when. `names` lists the name index; without it that part
    is a count."""
    parts = resolve_parts(parts)
    cats, sources = cache.get_detailed(domains, parts)
    out = []
    for d in domains:
        cat = cats[d]
        entry = {}
        for p in parts:
            source = sources[d].get(p, "absent")
            loaded = cat.has(p)
            entry[p] = {
                "source": source,
                "generation": _generation(cat, p) if loaded else None,
                "built_at": cat.part_built.get(p, cat.built_at) if loaded else None,
                "data": _part_data(cat, p, names) if loaded else None,
            }
        out.append({"domain": d, "generation": cat.fingerprint[0] if cat.fingerprint else 0,
                    "built_at": cat.built_at, "parts": entry})
    return {"domains": out}


def staleness(domains, parts) -> dict:
    """{domain: {part: reason}} for every part that would be rebuilt now; reads, never writes."""
    parts = resolve_parts(parts)
    fps = cache.fingerprints(domains)
    out: dict = {}
    for d in domains:
        cat = cache.cached(d)
        reasons = {}
        for p in parts:
            reason = cache.stale_reason(cat, p, fps[d])
            if reason:
                reasons[p] = reason
        out[d] = reasons
    return out


def refresh(domains, parts, dry_run: bool) -> dict:
    """Rebuild the stale `parts` of `domains` (all of them with no `parts`); `dry_run` only reports."""
    from artmind.cli_conventions import Change, Effect

    stale = staleness(domains, parts)
    todo = [f"{d}/{p}" for d, reasons in stale.items() for p in reasons]
    if not dry_run:
        for d, reasons in stale.items():
            if reasons:
                cache.rebuild([d], list(reasons))
    count = len(todo)
    effect = Effect(
        summary=(f"Rebuild {count} catalogue part(s) in the cache; the graph and the vault are not touched."
                 if count else "Every requested catalogue part is current; nothing to rebuild."),
        changes=[Change("catalogue_refresh", count, todo)] if count else [],
        reversible=True,
        records=["logs/plan-catalog/"] if count else [],
    )
    return {
        "domains": [{"domain": d, "stale": [{"part": p, "reason": r} for p, r in stale[d].items()]}
                    for d in domains],
        "refreshed": [] if dry_run else todo,
        "effect": effect.to_json(),
        **({"dry_run": True} if dry_run else {}),
    }
