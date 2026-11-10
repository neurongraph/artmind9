"""Peel 2 (spec 2026-11-09 §4, §4.1): what a domain holds and what already answers.

Pure over a loaded `DomainCatalogue` and schema facts, plus two file/registry reads (saved
views, structured tables). A part that was not read or failed is absent and named in `warnings`.
"""
from __future__ import annotations

from loguru import logger

VIEW_EXAMPLES = 2


def _classes(cat, facts: dict | None) -> list:
    declared = (facts or {}).get("classes") or {}
    types = dict(cat.types) if cat.has("types") else None
    out = []
    for name, count, _ in cat.classes:
        spec = declared.get(name) or {}
        row = {"class": name, "count": count, "kind": spec.get("kind"), "definition": spec.get("definition")}
        if types is not None:
            t = types.get(name)
            if t is not None:
                row["types"] = {"top": t["top"], "distinct": t["distinct"]}
                row["dated"] = round(t["dated"] / t["total"], 2) if t["total"] else 0.0
        out.append(row)
    return out


def _views(domain: str, warnings: list):
    from artmind.kg_views import store

    try:
        good, invalid = store.list_views([domain])
    except Exception as exc:
        warnings.append(f"{domain}: views unreadable: {exc}")
        return None
    if invalid:
        warnings.append(f"{domain}: {len(invalid)} invalid view(s) left out (artmind query views list)")
    out = []
    for loaded in good:
        spec = loaded.spec
        out.append({"name": spec.name, "summary": spec.summary,
                    "params": {n: {"type": p.type, "required": p.required,
                                   **({"entity_class": p.entity_class} if p.entity_class else {})}
                               for n, p in spec.params.items()},
                    "examples": list(spec.examples)[:VIEW_EXAMPLES]})
    return out


def _structured(domain: str, warnings: list):
    from artmind.structured import registry

    try:
        rows = registry.routing_surface([domain])
    except Exception as exc:
        logger.debug("orient: routing surface for {} failed: {}", domain, exc)
        warnings.append(f"{domain}: structured tables unreadable: {exc}")
        return None
    return [{"table": r["table"], "grain": r.get("grain"), "rows": r.get("row_count"),
             "bridge_classes": [e["entity_class"] for e in r.get("entity_classes") or []]} for r in rows]


def build_block(cat, facts: dict | None, *, card: dict, warnings: list) -> dict:
    d = cat.domain
    out: dict = {"domain": d, "card": card}
    if cat.has("classes"):
        out["classes"] = _classes(cat, facts)
        if not cat.has("types"):
            warnings.append(f"{d}: types not read")
    else:
        warnings.append(f"{d}: classes not read")
    if cat.has("shapes"):
        out["relations"] = [{"from": s, "rel": r, "to": t, "count": n} for s, r, t, n in cat.shapes]
    else:
        warnings.append(f"{d}: shapes not read")
    if cat.has("stores") and (cat.stores or {}).get("structured_tables"):
        tables = _structured(d, warnings)
        if tables is not None:
            out["structured"] = tables
    views = _views(d, warnings)
    if views is not None:
        out["views"] = views
    if cat.has("assets") and cat.assets is not None:
        out["topic_plans"] = [{"slug": a["id"], "topic": a["title"], "subjects": a["detail"].get("subjects") or []}
                              for a in cat.assets if a.get("kind") == "topic_plan" and a.get("status", "ok") == "ok"
                              and a.get("title")]
        detectors = sorted(a["id"] for a in cat.assets if a.get("kind") == "insight")
        as_of = max((str(a["detail"]["as_of"]) for a in cat.assets
                     if a.get("kind") == "insight" and a.get("detail", {}).get("as_of")), default=None)
        out["insights"] = {"as_of": as_of, "detectors": detectors} if detectors else None
    else:
        warnings.append(f"{d}: assets not read")
    if cat.has("versions"):
        out["documents"] = [dict(v) for v in cat.versions]
    else:
        warnings.append(f"{d}: versions not read")
    return out
