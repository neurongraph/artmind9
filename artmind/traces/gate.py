"""The classic band and the variant gate (spec 2026-11-08 §3.3-§3.4).

Verdicts are {seq: {"verdict", "better"}} with left = a baseline run, right = the other run. Cost and latency
are reported elsewhere and never gate.
"""

from __future__ import annotations


def balance(judgements: dict) -> int:
    """left-better minus right-better over judged questions (> 0: the baseline side better more often)."""
    js = [j for j in judgements.values() if j.get("verdict") not in (None, "unjudged")]
    return sum(1 for j in js if j.get("better") == "left") - sum(1 for j in js if j.get("better") == "right")


def _material(judgements: dict) -> int:
    return sum(1 for j in judgements.values() if j.get("verdict") == "material")


def band(baseline_pairs: dict) -> dict:
    """The widest |balance| and the highest material count over every pair of baseline runs."""
    if not baseline_pairs:
        return {"balance": 0, "material": 0}
    return {"balance": max(abs(balance(v)) for v in baseline_pairs.values()),
            "material": max(_material(v) for v in baseline_pairs.values())}


def evaluate_variant(band_: dict, variant_pairs: dict, *, baseline_found: dict, variant_found: dict) -> dict:
    """`variant_pairs`: baseline run id -> verdicts (left = that baseline run, right = the variant).
    `baseline_found`: run id -> {seq: set of must items found}; `variant_found`: {seq: set}."""
    b = band_["balance"]
    balances = {rid: balance(v) for rid, v in variant_pairs.items()}
    worse_sets = [{s for s, j in v.items() if j.get("verdict") == "material" and j.get("better") == "left"}
                  for v in variant_pairs.values()]
    worse_than_all = sorted(set.intersection(*worse_sets)) if worse_sets else []
    lost: dict[int, list[str]] = {}
    seqs = set().union(*(f.keys() for f in baseline_found.values())) if baseline_found else set()
    for seq in sorted(seqs):
        everyone = set.intersection(*(baseline_found[r].get(seq, set()) for r in baseline_found))
        missing = sorted(everyone - variant_found.get(seq, set()))
        if missing:
            lost[seq] = missing
    rows = [
        {"row": "within_band", "ok": all(x <= b for x in balances.values()),
         "detail": f"balance against each baseline run {balances}, band {b}"},
        {"row": "no_lost_must", "ok": not lost, "detail": f"must items every baseline run found, lost: {lost}"},
        {"row": "no_question_worse_than_all", "ok": not worse_than_all,
         "detail": f"materially worse than every baseline run: {worse_than_all}"},
    ]
    return {"passed": all(r["ok"] for r in rows),
            "above_band": bool(balances) and all(x < -b for x in balances.values()),
            "balances": balances, "worse_than_all": worse_than_all, "lost_must": lost, "rows": rows}
