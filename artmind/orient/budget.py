"""Orient's own budgets (spec 2026-11-09 §5.1). Every cut is returned in `cuts`; nothing is dropped
silently. Sizes are compact-JSON characters. Class definitions are cut last: they are what peel 2
is for (§4.1)."""
from __future__ import annotations

import copy
import json

CORPUS_BUDGET = 9000
DOMAIN_BUDGET = 12000
CALL_BUDGET = 24000
RELATIONS_KEEP = 20
TYPES_KEEP = 5
DOCUMENTS_KEEP = 10

# Cut records: `shown`/`total` per part. definitions: classes keeping a definition / classes with one;
# relations, documents: rows kept / rows; topic_plans: plans kept / plans; types: values kept per class /
# classes shortened; view_examples: examples kept (0) / views stripped.
CUT_ORDER = ["relations", "topic_plans", "types", "view_examples", "documents", "definitions"]


def size(x) -> int:
    return len(json.dumps(x, separators=(",", ":"), ensure_ascii=False, default=str))


def _rest(part: str, d: str) -> str:
    return {
        "relations": f"artmind query graph metadata --domain {d}",
        "topic_plans": f"artmind query topics plan --list --domain {d}",
        "types": f"artmind query graph entity-listing --domain {d} --countAll",
        "view_examples": "artmind query views show <name>",
        "documents": f"artmind query graph structural-metadata --domain {d}",
        "definitions": f"artmind query graph metadata --domain {d}",
    }[part]


def _cut(block: dict, part: str, limit: int) -> dict | None:
    """Apply one cut step to `block` in place; return the cut record or None when it removed nothing."""
    d = block.get("domain")
    classes = block.get("classes") or []
    if part == "types":
        hit = 0
        for c in classes:
            top = (c.get("types") or {}).get("top")
            if top and len(top) > TYPES_KEEP:
                c["types"]["top"] = top[:TYPES_KEEP]
                hit += 1
        return {"part": part, "domain": d, "shown": TYPES_KEEP, "total": hit} if hit else None
    if part == "view_examples":
        views = [v for v in block.get("views") or [] if v.get("examples")]
        for v in views:
            v["examples"] = []
        return {"part": part, "domain": d, "shown": 0, "total": len(views)} if views else None
    if part == "definitions":
        defined = [c for c in classes if c.get("definition")]
        removed = 0
        for c in sorted(defined, key=lambda c: (c["count"], c["class"])):
            if size(block) <= limit:
                break
            c["definition"] = None
            removed += 1
        return {"part": part, "domain": d, "shown": len(defined) - removed, "total": len(defined)} if removed else None
    key, keep = {"relations": ("relations", RELATIONS_KEEP), "topic_plans": ("topic_plans", 0),
                 "documents": ("documents", DOCUMENTS_KEEP)}[part]
    holder = block.get("levels") if part == "topic_plans" else block
    rows = holder.get(key) if isinstance(holder, dict) else None
    if not isinstance(rows, list) or len(rows) <= keep:
        return None
    total = len(rows)
    holder[key] = rows[:keep]
    if part == "topic_plans":
        holder["topic_plans_total"] = total
    return {"part": part, "domain": d, "shown": keep, "total": total}


def _fit_block(block: dict, limit: int) -> list:
    cuts = []
    for part in CUT_ORDER:
        if size(block) <= limit:
            break
        rec = _cut(block, part, limit)
        if rec:
            rec["rest"] = _rest(part, block.get("domain"))
            cuts.append(rec)
    return cuts


def fit_domains(blocks: list, *, domain_budget: int = DOMAIN_BUDGET, call_budget: int = CALL_BUDGET) -> tuple:
    blocks = copy.deepcopy(blocks)
    cuts: list = []
    for b in blocks:
        cuts += _fit_block(b, domain_budget)
    exhausted: set = set()
    while size(blocks) > call_budget:
        live = [b for b in blocks if id(b) not in exhausted]
        if not live:
            break  # nothing left to cut: the payload stays over budget and `cuts` says what was cut
        largest = max(live, key=size)
        before = size(largest)
        target = max(1, before - (size(blocks) - call_budget))
        new = _fit_block(largest, target)
        if not new or size(largest) >= before:
            exhausted.add(id(largest))
            continue
        cuts += new
    merged: dict = {}
    for c in cuts:  # one record per (domain, part): the first pass's total, the latest pass's shown
        key = (c["domain"], c["part"])
        merged[key] = {**c, "total": merged[key]["total"]} if key in merged else c
    out = sorted(merged.values(), key=lambda c: (c["domain"], CUT_ORDER.index(c["part"])))
    return blocks, out


def fit_corpus(cards: list, *, corpus_budget: int = CORPUS_BUDGET) -> tuple:
    rows = list(cards)
    cuts: list = []
    families: dict = {}
    for c in cards:
        if c.get("family"):
            families.setdefault(c["family"], []).append(c)
    for root, members in sorted(families.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        if size(rows) <= corpus_budget:
            break
        names = [m["domain"] for m in members]
        rows = [r for r in rows if r.get("domain") not in names]
        rows.append({"family": root, "collapsed": True, "domains": names,
                     "documents": sum((m.get("unit") or {}).get("documents", 0) for m in members),
                     "entities": sum(m.get("entities", 0) for m in members),
                     "stores": sorted({s for m in members for s in m.get("stores", [])})})
        cuts.append({"part": "cards", "domain": root, "shown": 0, "total": len(members),
                     "rest": f"artmind query orient --domain {root}"})
    rows.sort(key=lambda r: r.get("domain") or r.get("family"))
    return rows, cuts
