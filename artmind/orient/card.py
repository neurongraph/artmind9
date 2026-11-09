"""Peel 1 (spec 2026-11-09 §2.2, §3): one card per domain, the family tree and the corpus block.

Pure: takes a loaded `DomainCatalogue`, the schema facts and the narrator, returns dicts.
A part the catalogue did not load is left out of the card and named in `warnings`; `null`
always means "declared absent" (no narrator, no filename rule, no insights snapshot).
"""
from __future__ import annotations

VOCABULARY = 5
_COVERAGE = ("behind_vault", "summaries_missing")


def family_tree(domains) -> dict:
    """{root: [children]} over every domain name; a root with no children maps to []."""
    names = sorted(set(domains))
    tree: dict = {}
    for d in names:
        root = d.split(".", 1)[0]
        tree.setdefault(root, [])
        if d != root:
            tree[root].append(d)
    return tree


def family_of(domain: str, tree: dict) -> tuple:
    """(family root, sibling count) for a dotted child; (None, 0) for a root."""
    root = domain.split(".", 1)[0]
    if domain == root:
        return None, 0
    return root, max(0, len(tree.get(root, [])) - 1)


def _summaries(records) -> str:
    total = len(records)
    have = sum(1 for r in records if r.has_summary)
    if not total or have == total:
        return "all" if total else "none"
    return "none" if have == 0 else f"{have} of {total}"


def _insights_as_of(assets) -> str | None:
    dates = [str(a.get("detail", {}).get("as_of")) for a in assets
             if a.get("kind") == "insight" and a.get("detail", {}).get("as_of")]
    return max(dates) if dates else None


def _stores(stores: dict) -> list:
    out = ["graph" if stores.get("graph_extraction", True) else "chunks"]
    if stores.get("structured_tables"):
        out.append("structured")
    return out


def _missing(warnings, domain: str, part: str) -> None:
    if warnings is not None:
        warnings.append(f"{domain}: {part} not read")


def build_card(cat, facts: dict | None, narrator: dict | None, family: tuple, *, children: int = 0,
               warnings: list | None = None) -> dict:
    d = cat.domain
    root, siblings = family
    c: dict = {"domain": d, "family": root, "siblings": siblings}
    if children:
        c["rolls_up"] = children  # catalogue parts include the dotted children
    if facts is None:
        if warnings is not None:
            warnings.append(f"{d}: schema not found; purpose, dating rule and class definitions unknown")
    elif facts.get("purpose"):
        c["purpose"] = facts["purpose"]
    if cat.has("stores") and cat.stores is not None:
        c["kind"] = "graph" if cat.stores.get("graph_extraction", True) else "chunks-only"
        c["stores"] = _stores(cat.stores)
    else:
        _missing(warnings, d, "stores")
    if cat.has("documents"):
        records = sorted(cat.doc_records, key=lambda r: r.name)
        c["unit"] = {"documents": len(records), "example": records[0].name if records else None}
        if facts is not None:
            c["unit"]["pattern"] = facts.get("filename_pattern")
    else:
        _missing(warnings, d, "documents")
    c["voice"] = (None if narrator is None else
                  {"narrator": narrator["name"], "entity_class": narrator["entity_class"], "first_person": True})
    if cat.has("time") and cat.time is not None:
        t = cat.time
        time_block = {
            "dated": {"from_content": int(t.get("from_content", 0)),
                      "defaulted_to_ingestion": int(t.get("documents", 0)) - int(t.get("from_content", 0))},
            "span": ([t["content_earliest"], t["content_latest"]]
                     if t.get("content_earliest") and t.get("content_latest") else None),
        }
        if facts is not None:
            time_block["rule"] = facts.get("time_rule") or {}
        if cat.has("versions"):
            time_block["versions"] = {"with_successor": sum(1 for v in cat.versions if v.get("successor")),
                                      "retired": sum(1 for v in cat.versions if v["status"] == "retired")}
        else:
            _missing(warnings, d, "versions")
        c["time"] = time_block
    else:
        _missing(warnings, d, "time")
    if cat.has("classes"):
        c["vocabulary"] = [name for name, _, _ in cat.classes[:VOCABULARY]]
    else:
        _missing(warnings, d, "classes")
    if len(cat.fingerprint) > 1:
        c["entities"] = int(cat.fingerprint[1])
    else:
        _missing(warnings, d, "entity count")
    if cat.has("assets") and cat.assets is not None:
        kinds = [a.get("kind") for a in cat.assets]
        c["assets"] = {"views": kinds.count("view"), "topic_pages": kinds.count("topic"),
                       "insights": _insights_as_of(cat.assets),
                       "summaries": _summaries(cat.doc_records) if cat.has("documents") else None}
    else:
        _missing(warnings, d, "assets")
    if cat.has("health") and cat.health:
        coverage = {k: int((cat.health.get(k) or {}).get("count") or 0) for k in _COVERAGE}
        coverage = {k: v for k, v in coverage.items() if v > 0}
        if coverage:
            c["coverage"] = coverage
    return c


def corpus_block(cards: list, tree: dict, *, generation: int, built_at: str) -> dict:
    """Totals over the top cards only: a card whose ancestor also has a card is already inside it."""
    names = {c["domain"] for c in cards}
    top = [c for c in cards if not any(c["domain"].startswith(n + ".") for n in names)]
    spans = [c["time"]["span"] for c in top if (c.get("time") or {}).get("span")]
    partial = [c["domain"] for c in top if not all(k in c for k in ("unit", "time", "entities"))]
    out = {
        "domains": len(cards),
        "families": tree,
        "documents": sum((c.get("unit") or {}).get("documents", 0) for c in top),
        "entities": sum(c.get("entities", 0) for c in top),
        "span": [min(s[0] for s in spans), max(s[1] for s in spans)] if spans else None,
        "generation": generation,
        "built_at": built_at,
    }
    if partial:
        out["partial"] = partial
    return out
