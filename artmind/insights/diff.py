"""Diff a run's findings against the previous snapshot (spec §9.3)."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import replace

from artmind.insights.graphs import jaccard
from artmind.insights.model import Finding

REKEY_JACCARD = 0.3
SCORE_DELTA = 0.15
HEADLINE_DELTA = 0.25


def _prefix(key: str) -> str:
    return key.rsplit(":", 1)[0]


def _rekey(current: list[Finding], previous: list[Finding]) -> None:
    """A membership finding (circle, share) whose key is new adopts the previous key
    of the most similar membership finding with the same prefix (Jaccard >= 0.3)."""
    prev_keys = {p.key for p in previous}
    by_prefix: dict[str, list[Finding]] = defaultdict(list)
    for p in previous:
        if "members" in p.stats:
            by_prefix[_prefix(p.key)].append(p)
    taken = {c.key for c in current}
    for c in current:
        if "members" not in c.stats or c.key in prev_keys:
            continue
        best = None
        for p in by_prefix.get(_prefix(c.key), []):
            if p.key in taken:
                continue
            score = jaccard(set(c.stats["members"]), set(p.stats["members"]))
            if score >= REKEY_JACCARD and (best is None or score > best[0]):
                best = (score, p.key)
        if best:
            taken.discard(c.key)
            c.key = best[1]
            taken.add(c.key)


def _changed(cur: Finding, prev: Finding) -> bool:
    if abs(cur.score - prev.score) >= SCORE_DELTA:
        return True
    a, b = cur.stats.get("headline"), prev.stats.get("headline")
    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (a, b))
    if not numeric:
        return False
    base = max(abs(a), abs(b))
    return base > 0 and abs(a - b) / base >= HEADLINE_DELTA


def apply_diff(current: list[Finding], previous: list[Finding] | None, previous_name: str | None):
    """Mark each current finding new/changed/same and append previous findings now gone.
    Findings already `gone` in the previous run are dropped for good."""
    if previous is None:
        for c in current:
            c.diff = "new"
        return current, {"previous": None, "new": len(current), "changed": 0, "same": 0, "gone": 0}
    live = [p for p in previous if p.diff != "gone"]
    _rekey(current, live)
    prev_map = {p.key: p for p in live}
    counts: Counter = Counter()
    for c in current:
        p = prev_map.get(c.key)
        c.diff = "new" if p is None else ("changed" if _changed(c, p) else "same")
        counts[c.diff] += 1
    current_keys = {c.key for c in current}
    gone = [replace(p, diff="gone") for p in live if p.key not in current_keys]
    return current + gone, {
        "previous": previous_name, "new": counts["new"], "changed": counts["changed"],
        "same": counts["same"], "gone": len(gone),
    }
