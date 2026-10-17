"""same_as_candidates: probable duplicate entities within one class (spec §6.8).

Candidates come from names:
- kinship/alias groups;
- initials;
- a token subset;
- a small edit distance.

Each candidate is scored by a fixed logistic over:
- neighbourhood similarity in the co-mention graph;
- how often the pair is co-mentioned (duplicates rarely are);
- time-span overlap;
- agreement of `type` / `relationship_to_*`.

Nothing merges: findings feed `artmind insights same-as --export` and the curation review queue.
"""

from __future__ import annotations

import itertools
import math
import re
from collections import defaultdict
from pathlib import Path

import yaml

from artmind.insights import graphs
from artmind.insights.detectors import Detector, RunContext, Skip, subject
from artmind.insights.model import Finding
from artmind.insights.stats import days_between, levenshtein

ALIASES_FILE = Path(__file__).resolve().parent.parent / "aliases.yaml"
_PUNCT = re.compile(r"[^\w\s]")
MAX_TOKEN_BUCKET = 50   # a token shared by more entities than this ("office") is not a signal
MIN_EDIT_LEN = 4
AMBIGUITY_MARGIN = 0.15   # a short name matching several entities needs a best pair this much ahead
RELATIONAL = frozenset({"father", "mother", "wife", "husband", "son", "daughter", "brother", "sister", "uncle",
                        "aunt", "friend", "colleague", "boss", "team", "family", "kids", "children", "parents",
                        "law"})   # "in-law" normalises to "in law"


def norm(name) -> str:
    return " ".join(_PUNCT.sub(" ", str(name).casefold()).split())


def load_alias_groups(config=None) -> list[frozenset[str]]:
    groups = yaml.safe_load(ALIASES_FILE.read_text(encoding="utf-8")) or []
    if config is not None:
        groups = groups + (config.detector("same_as_candidates").get("aliases") or [])
    return [frozenset(norm(n) for n in g) for g in groups]


def _initials(n: str) -> str:
    tokens = n.split()
    return "".join(t[0] for t in tokens) if len(tokens) >= 2 else ""


def _possessive(raw) -> bool:
    """'X's Father' denotes someone related to X, never X; checked on the raw name (norm drops the apostrophe)."""
    r = str(raw).casefold().replace("\u2019", "'")
    return "'s " in r or r.endswith("'s")


def name_match(a: str, b: str, aliases: list[frozenset[str]], max_dist: float,
               kinship_ok: bool = True) -> tuple[str, float] | None:
    """(rule, strength) when two normalised names plausibly denote one thing."""
    if a == b:
        return ("exact", 1.0)
    if kinship_ok and any(a in g and b in g for g in aliases):
        return ("kinship", 1.0)
    ta, tb = set(a.split()), set(b.split())
    for short, long_ in ((a, b), (b, a)):
        if " " not in short and 2 <= len(short) <= 4 and short == _initials(long_):
            return ("initials", 0.9)
    if ta < tb or tb < ta:
        if (ta ^ tb) & RELATIONAL:
            return None
        return ("token_subset", 0.8)
    if min(len(a), len(b)) >= MIN_EDIT_LEN and a[0] == b[0]:
        if abs(len(a) - len(b)) / max(len(a), len(b)) > max_dist:
            return None
        d = levenshtein(a, b) / max(len(a), len(b))
        if d <= max_dist:
            return ("edit", round(0.7 * (1 - d / max_dist) + 0.2, 3))
    return None


def candidate_pairs(rows, aliases, max_dist, kinship_ok: bool = True) -> list[tuple[str, str, str, float]]:
    """[(id_a, id_b, rule, strength)] with id_a < id_b, from blocked comparisons only."""
    named = sorted(((e._id, norm(e.name)) for e in rows if norm(e.name) and not _possessive(e.name)),
                   key=lambda x: x[0])
    names = dict(named)
    buckets: list[list[str]] = []
    by_token, by_first, by_initials, by_alias = defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    for _id, n in named:
        for t in set(n.split()):
            by_token[t].append(_id)
        by_first[n[0]].append(_id)
        if _initials(n):
            by_initials[_initials(n)].append(_id)
        for i, g in enumerate(aliases):
            if n in g:
                by_alias[i].append(_id)
    buckets += [b for b in by_token.values() if len(b) <= MAX_TOKEN_BUCKET]
    buckets += list(by_alias.values())
    buckets += [b for b in by_first.values()]
    pairs: set[tuple[str, str]] = set()
    for b in buckets:
        pairs.update(tuple(sorted(p)) for p in itertools.combinations(b, 2))
    for _id, n in named:
        if " " not in n and n in by_initials:
            pairs.update(tuple(sorted((_id, other))) for other in by_initials[n] if other != _id)
    out = []
    for a, b in sorted(pairs):
        match = name_match(names[a], names[b], aliases, max_dist, kinship_ok)
        if match:
            out.append((a, b, *match))
    return out


def _span_overlap(da: tuple[str, ...], db: tuple[str, ...]) -> float:
    start, end = max(da[0], db[0]), min(da[-1], db[-1])
    if end < start:
        return 0.0
    shorter = min(days_between(da[0], da[-1]), days_between(db[0], db[-1]))
    return 1.0 if shorter == 0 else min(1.0, days_between(start, end) / shorter)


def _resolve_ambiguity(scored: list[tuple], by_id: dict) -> list[tuple]:
    """A short name on the token-subset side ("Sam" vs "Sam Rivera", "Sam Okoro") that matches several
    entities keeps only its one clearly best pair; when no pair is clearly ahead, all of its pairs are
    dropped. Initials matches ("TPO" / "Tech Park One") are specific and never compete here."""
    def short_side(a, b, rule):
        if rule != "token_subset":
            return None
        ta, tb = norm(by_id[a].name).split(), norm(by_id[b].name).split()
        return a if len(ta) < len(tb) else b

    groups = defaultdict(list)
    for item in scored:
        side = short_side(item[0], item[1], item[2])
        if side is not None:
            groups[side].append(item)
    drop = set()
    for items in groups.values():
        if len(items) < 2:
            continue
        ranked = sorted(items, key=lambda i: (-i[4], i[0], i[1]))
        keep = ranked[0] if ranked[0][4] - ranked[1][4] >= AMBIGUITY_MARGIN else None
        drop.update((i[0], i[1]) for i in items if i is not keep)
    return [i for i in scored if (i[0], i[1]) not in drop]


def _plan(ctx: RunContext):
    classes = [c for c in ctx.classes_of_kind("recurrent") if len(ctx.members(c, floor=1)) >= 2]
    return classes or Skip("no recurrent class with two dated entities")


def _run(ctx: RunContext, cls: str) -> list[Finding]:
    cfg = ctx.config.detector("same_as_candidates")
    min_score, max_dist = cfg.get("min_score", 0.5), cfg.get("max_name_distance", 0.25)
    rows = ctx.members(cls, floor=cfg.get("min_days", 1))
    by_id = {e._id: e for e in rows}
    props = [p for p in sorted(ctx.profile.classes[cls].properties) if p.startswith("relationship_to")]
    kinship_ok = cls == "PERSON" or bool(props)
    pairs = candidate_pairs(rows, load_alias_groups(ctx.config), max_dist, kinship_ok)
    if not pairs:
        return []
    try:
        g = graphs.co_mention_graph(rows, min_weight=1)
    except graphs.GraphTooLarge:
        g = None
    scored = []
    for a, b, rule, strength in pairs:
        ea, eb = by_id[a], by_id[b]
        nb = graphs.jaccard(set(g[a]) - {b}, set(g[b]) - {a}) if g is not None and a in g and b in g else 0.0
        co = len(set(ea.days) & set(eb.days))
        overlap = _span_overlap(ea.days, eb.days)
        agree = disagree = 0
        pairs_to_compare = [(ea.type, eb.type)] + [
            (ctx.entity_props.get((cls, p), {}).get(a), ctx.entity_props.get((cls, p), {}).get(b)) for p in props
        ]
        for va, vb in pairs_to_compare:
            if va and vb:
                if norm(va) == norm(vb):
                    agree += 1
                else:
                    disagree += 1
        z = (-2.0 + 2.5 * strength + 2.0 * nb - 1.5 * min(1.0, co / 2) + 0.5 * overlap
             + 0.6 * min(agree, 2) - 0.8 * min(disagree, 2))
        score = round(1 / (1 + math.exp(-z)), 4)
        scored.append((a, b, rule, strength, score, nb, co, overlap, agree, disagree))
    out = []
    for a, b, rule, strength, score, nb, co, overlap, agree, disagree in _resolve_ambiguity(scored, by_id):
        if score < min_score:
            continue
        ea, eb = by_id[a], by_id[b]
        canonical, alias = sorted((ea, eb), key=lambda e: (-len(e.days), -len(e.name), e._id))
        ctx.memo.setdefault("same_as", []).append(
            {"class": cls, "canonical": canonical._id, "alias": alias._id, "score": score})
        out.append(Finding(
            key=f"same_as_candidates:{cls}:{a}:{b}", detector="same_as_candidates", kind="health", score=score,
            title=f"Possibly the same {cls}: {alias.name} / {canonical.name}",
            subjects=[subject(canonical), subject(alias)],
            stats={"canonical": subject(canonical), "alias": subject(alias), "rule": rule,
                   "name_strength": strength, "neighbour_jaccard": round(nb, 3), "co_mentions": co,
                   "time_overlap": round(overlap, 3), "agree": agree, "disagree": disagree, "headline": score},
            card={"type": "stat"}, action="curate",
        ))
    return out


SAME_AS_CANDIDATES = Detector("same_as_candidates", "health", _plan, _run)
