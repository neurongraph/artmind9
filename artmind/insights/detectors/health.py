"""Graph-health detectors (spec §7): where the graph and its schema disagree.

Each finding carries an `action` (schema | curate) pointing at the fix.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

import networkx as nx

from artmind.insights.detectors import Detector, RunContext, Skip
from artmind.insights.model import Finding
from artmind.insights.profile import declared_triples


def _all(ctx: RunContext) -> list[str]:
    return ["all"]


# ── kind_mismatch ─────────────────────────────────────────────────────────────

def _run_kind(ctx: RunContext, _target: str) -> list[Finding]:
    out = []
    for cls, cp in sorted(ctx.profile.classes.items()):
        if not cp.declared_kind or cp.effective_kind == cp.declared_kind:
            continue
        score = cp.single_day_share if cp.effective_kind == "occurrent" else min(1.0, cp.median_days / 10)
        out.append(Finding(
            key=f"kind_mismatch:{cls}", detector="kind_mismatch", kind="health", score=round(score, 4),
            title=f"{cls} is declared {cp.declared_kind} but behaves {cp.effective_kind}",
            stats={"declared": cp.declared_kind, "effective": cp.effective_kind, "entities": cp.entities,
                   "median_days": cp.median_days, "single_day_share": cp.single_day_share, "headline": cp.median_days},
            card={"type": "stat"}, action="schema",
        ))
    return out


# ── vocabulary_sprawl ─────────────────────────────────────────────────────────

_PROSE_STARTS = {"what", "how", "why", "who", "when", "where", "was", "is", "did", "does", "if", "whether"}


def hint_options(hint) -> list[str]:
    """A hint written as a closed list ('mild, moderate, strong') -> its options; else []."""
    if not isinstance(hint, str) or "e.g" in hint.lower():
        return []
    parts = [p.strip() for p in re.split(r",|\bor\b", hint) if p.strip()]
    if any(p.split()[0].lower() in _PROSE_STARTS for p in parts):
        return []
    if len(parts) >= 2 and all(len(p.split()) <= 3 for p in parts):
        return parts
    return []


def _norm(value: str) -> str:
    return re.sub(r"[\s_\-]+", " ", value.strip().casefold())


def _near_duplicates(sample: list[str]) -> list[list[str]]:
    groups: dict[str, set[str]] = defaultdict(set)
    for v in sample:
        groups[_norm(v)].add(v)
    return sorted(sorted(g) for g in groups.values() if len(g) > 1)[:10]


def _expected(schema: dict, cls: str, prop: str) -> tuple[int, str] | None:
    decl = (schema.get("entity_types") or {}).get(cls) or {}
    if prop == "type":
        examples = decl.get("type_examples") or []
        return (len(examples) * 5, "type_examples") if examples else (40, "no type_examples")
    spec = (decl.get("properties") or {}).get(prop)
    options = hint_options(spec.get("hint") if isinstance(spec, dict) else None)
    return (max(10, len(options) * 5), "hint options") if options else None


def _run_sprawl(ctx: RunContext, _target: str) -> list[Finding]:
    out = []
    for cls, cp in sorted(ctx.profile.classes.items()):
        for prop, st in sorted(cp.properties.items()):
            expected = _expected(ctx.schema, cls, prop)
            if expected is None:
                continue
            limit, basis = expected
            dups = _near_duplicates(st.get("sample") or [])
            distinct = st.get("distinct", 0)
            if distinct <= limit and not dups:
                continue
            score = min(1.0, distinct / (limit * 4)) if distinct > limit else 0.3
            out.append(Finding(
                key=f"vocabulary_sprawl:{cls}:{prop}", detector="vocabulary_sprawl", kind="health",
                score=round(score, 4), title=f"{cls}.{prop} has {distinct} distinct values",
                stats={"distinct": distinct, "expected_max": limit, "basis": basis, "near_duplicates": dups,
                       "sample": (st.get("sample") or [])[:20], "headline": distinct},
                card={"type": "stat"}, action="schema",
            ))
    return out


# ── relationship_usage ────────────────────────────────────────────────────────

def _run_relations(ctx: RunContext, _target: str) -> list[Finding]:
    cfg = ctx.config.detector("relationship_usage")
    min_count, min_overlap_n = cfg.get("min_count", 5), cfg.get("min_overlap_count", 10)
    rels = [r for r in ctx.profile.relations if r.get("rel") is not None]
    total = sum(r["n"] for r in rels) or 1
    out = []
    undeclared = [r for r in rels if not r["declared"] and r["n"] >= min_count]
    if undeclared:
        out.append(Finding(
            key="relationship_usage:undeclared", detector="relationship_usage", kind="health",
            score=round(min(1.0, 2 * sum(r["n"] for r in undeclared) / total), 4),
            title=f"{len(undeclared)} relationship triples are used but not declared",
            stats={"triples": [{k: r[k] for k in ("rel", "src", "dst", "n")} for r in undeclared[:25]],
                   "count": len(undeclared), "headline": len(undeclared)},
            card={"type": "ranking"}, action="schema",
        ))
    declared = declared_triples(ctx.schema)
    observed = {(r["rel"], r["src"], r["dst"]) for r in rels}
    unused = sorted(declared - observed, key=lambda t: tuple(map(str, t)))
    if unused and rels:
        out.append(Finding(
            key="relationship_usage:unused", detector="relationship_usage", kind="health",
            score=round(min(1.0, len(unused) / len(declared)), 4),
            title=f"{len(unused)} declared relationships were never extracted",
            stats={"triples": [{"rel": r, "src": s, "dst": d} for r, s, d in unused[:50]],
                   "count": len(unused), "headline": len(unused)},
            card={"type": "ranking"}, action="schema",
        ))
    by_ends: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rels:
        if r["n"] >= min_overlap_n and r["targets"]:
            by_ends[(r["src"], r["dst"])].append(r)
    for (src, dst), group in sorted(by_ends.items()):
        group = sorted(group, key=lambda r: str(r["rel"]))
        for i, r1 in enumerate(group):
            for r2 in group[i + 1:]:
                a, b = set(r1["targets"]), set(r2["targets"])
                overlap = len(a & b) / len(a | b)
                if overlap < cfg.get("min_jaccard", 0.3):
                    continue
                out.append(Finding(
                    key=f"relationship_usage:overlap:{src}:{r1['rel']}:{r2['rel']}:{dst}",
                    detector="relationship_usage", kind="health", score=round(overlap, 4),
                    title=f"{r1['rel']} and {r2['rel']} ({src} -> {dst}) point at the same targets",
                    stats={"overlap": round(overlap, 3), "counts": {r1["rel"]: r1["n"], r2["rel"]: r2["n"]},
                           "headline": round(overlap, 3)},
                    card={"type": "stat"}, action="schema",
                ))
    return out


# ── open_conflicts ────────────────────────────────────────────────────────────

def _run_conflicts(ctx: RunContext, _target: str) -> list[Finding]:
    by_cls: dict[str, dict[str, int]] = defaultdict(dict)
    for r in ctx.profile.open_conflicts:
        by_cls[r["cls"] or "UNKNOWN"][str(r["property"])] = r["n"]
    out = []
    for cls, props in sorted(by_cls.items()):
        n = sum(props.values())
        top = dict(sorted(props.items(), key=lambda kv: (-kv[1], kv[0]))[:10])
        out.append(Finding(
            key=f"open_conflicts:{cls}", detector="open_conflicts", kind="health",
            score=round(min(1.0, n / 100), 4), title=f"{n} open conflicts on {cls}",
            stats={"open": n, "by_property": top, "headline": n}, card={"type": "ranking"}, action="curate",
        ))
    return out


KIND_MISMATCH = Detector("kind_mismatch", "health", _all, _run_kind)
VOCABULARY_SPRAWL = Detector("vocabulary_sprawl", "health", _all, _run_sprawl)
RELATIONSHIP_USAGE = Detector("relationship_usage", "health", _all, _run_relations)
OPEN_CONFLICTS = Detector("open_conflicts", "health", _all, _run_conflicts)
# ── attribution_suspects ──────────────────────────────────────────────────────

def _plan_attribution(ctx: RunContext):
    return ["anchor"] if ctx.anchor_relations else Skip("no anchor relations")


def _run_attribution(ctx: RunContext, _target: str) -> list[Finding]:
    """On a focused anchor relation (its top-3 target (class, type) combos cover >= focus_share),
    targets whose (class, type) is rare (< max_share) were probably attributed to the wrong subject."""
    cfg = ctx.config.detector("attribution_suspects")
    min_targets, max_share, focus = cfg.get("min_targets", 20), cfg.get("max_share", 0.05), cfg.get("focus_share", 0.8)
    by_rel: dict[str, list[dict]] = defaultdict(list)
    for r in ctx.anchor_relations:
        by_rel[r["rel"]].append(r)
    out = []
    for rel, rows in sorted(by_rel.items()):
        if len(rows) < min_targets:
            continue
        combo = {r["_id"]: (r["cls"], (r["type"] or "").strip().lower() or "(none)") for r in rows}
        counts = Counter(combo[r["_id"]] for r in sorted(rows, key=lambda r: r["_id"]))
        ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
        n = len(rows)
        if sum(c for _, c in ranked[:3]) / n < focus:
            continue
        top3 = {k for k, _ in ranked[:3]}
        rare = {k for k, c in counts.items() if c / n < max_share and k not in top3}
        suspects = sorted((r for r in rows if combo[r["_id"]] in rare),
                          key=lambda r: (r["cls"], str(r["name"]), r["_id"]))
        if not suspects:
            continue
        out.append(Finding(
            key=f"attribution_suspects:{rel}", detector="attribution_suspects", kind="health",
            score=round(min(1.0, 5 * len(suspects) / n), 4),
            title=f"{len(suspects)} {rel} targets look misattributed",
            subjects=[{"_id": r["_id"], "name": r["name"], "class": r["cls"]} for r in suspects[:10]],
            stats={"targets": n,
                   "suspects": [{"_id": r["_id"], "name": r["name"], "class": r["cls"], "type": r["type"]}
                                for r in suspects[:20]],
                   "typical": [{"class": c, "type": t, "count": k} for (c, t), k in ranked[:3]],
                   "headline": len(suspects)},
            card={"type": "ranking"}, action="reextract",
        ))
    return out


# ── fragments ─────────────────────────────────────────────────────────────────

def _plan_fragments(ctx: RunContext):
    return ["all"] if ctx.profile.entity_edges else Skip("no entity relations")


def _run_fragments(ctx: RunContext, _target: str) -> list[Finding]:
    cfg = ctx.config.detector("fragments")
    lo, hi = cfg.get("min_size", 3), cfg.get("max_size", 5)
    g = nx.Graph()
    g.add_edges_from(ctx.profile.entity_edges)
    comps = sorted((sorted(c) for c in nx.connected_components(g)), key=lambda c: (-len(c), c[0]))
    small = [c for c in comps[1:] if lo <= len(c) <= hi]
    if not small:
        return []
    entities = {e._id: e for e in ctx.entities}
    def describe(i):
        e = entities.get(i)
        return {"_id": i, "name": e.name if e else i, "class": e.cls if e else None}
    return [Finding(
        key="fragments:all", detector="fragments", kind="health", score=round(min(1.0, len(small) / 50), 4),
        title=f"{len(small)} small groups of entities are cut off from the main graph",
        stats={"count": len(small), "main_component": len(comps[0]),
               "sizes": {str(k): v for k, v in sorted(Counter(len(c) for c in small).items())},
               "examples": [[describe(i) for i in c] for c in small[:20]], "headline": len(small)},
        card={"type": "ranking"}, action="curate",
    )]


ATTRIBUTION_SUSPECTS = Detector("attribution_suspects", "health", _plan_attribution, _run_attribution)
FRAGMENTS = Detector("fragments", "health", _plan_fragments, _run_fragments)
