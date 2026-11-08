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


def evaluate_variant(band_: dict, variant_pairs: dict, *, baseline_found: dict, variant_found: dict | None) -> dict:
    """`variant_pairs`: baseline run id -> verdicts keyed by question (left = that baseline run, right = the variant).
    `baseline_found`: run id -> {question key: set of must items found}, only for questions that run asked and
    that were graded; `variant_found`: the same for the variant, or None when answers were not graded.

    Row details carry counts only. Must-item texts are in `lost_must_items`, for the private detail file;
    keys are whatever the caller uses (the CLI uses normalised question text and translates to qids)."""
    b = band_["balance"]
    balances = {rid: balance(v) for rid, v in variant_pairs.items()}
    keys = set().union(*(v.keys() for v in variant_pairs.values())) if variant_pairs else set()
    worse_than_all = []
    for k in sorted(keys, key=str):
        js = [v[k] for v in variant_pairs.values() if k in v]
        if js and all(j.get("verdict") == "material" and j.get("better") == "left" for j in js):
            worse_than_all.append(k)
    graded = variant_found is not None
    lost: dict = {}
    variant_missing: list = []
    if graded:
        for k in sorted(set().union(*(f.keys() for f in baseline_found.values())) if baseline_found else set(),
                        key=str):
            sets = [f[k] for f in baseline_found.values() if k in f]
            everyone = set.intersection(*sets) if sets else set()
            if k not in variant_found:
                if everyone:
                    variant_missing.append(k)
                continue
            missing = sorted(everyone - variant_found[k])
            if missing:
                lost[k] = missing
    lost_n = sum(len(v) for v in lost.values())
    rows = [
        {"row": "within_band", "ok": all(x <= b for x in balances.values()),
         "detail": f"balance against each baseline run {balances}, band {b}"},
        {"row": "no_lost_must", "ok": (not lost) if graded else None,
         "detail": (f"{lost_n} must item(s) every asking baseline run found are lost, over {len(lost)} question(s)"
                    if graded else "not graded")},
        {"row": "no_question_worse_than_all", "ok": not worse_than_all,
         "detail": f"{len(worse_than_all)} question(s) materially worse than every baseline run that asked them"},
    ]
    return {"passed": all(r["ok"] for r in rows if r["ok"] is not None),
            "above_band": bool(balances) and all(x < -b for x in balances.values()),
            "graded": graded, "balances": balances, "worse_than_all": worse_than_all,
            "lost_must_items": lost, "variant_missing": variant_missing, "rows": rows}
