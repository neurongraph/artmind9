"""The P3b gate (spec 2026-11-07 P3 §9.2, with the variance band): three runs on one build, classic A,
classic B and bundle, judged pairwise. A row with `ok: None` was not measured (e.g. no ratings) and does
not fail the gate; the gate passes when every measured row is ok."""

from __future__ import annotations


def balance(judgements: dict) -> int:
    """left-better minus right-better over judged questions."""
    js = [j for j in judgements.values() if j.get("verdict") not in (None, "unjudged")]
    return sum(1 for j in js if j.get("better") == "left") - sum(1 for j in js if j.get("better") == "right")


def _material_left(judgements: dict) -> set[int]:
    return {seq for seq, j in judgements.items() if j.get("verdict") == "material" and j.get("better") == "left"}


def evaluate(classic_a: dict, classic_b: dict, bundle: dict, ab: dict, a_bundle: dict, b_bundle: dict) -> dict:
    band = abs(balance(ab))
    bal = balance(a_bundle)                      # > 0: classic A better more often than bundle
    worse_both = sorted(_material_left(a_bundle) & _material_left(b_bundle))
    rows = []

    def row(name, ok, detail):
        rows.append({"row": name, "ok": ok, "detail": detail})

    rated = all(m["ratings"]["unrated"] < m["questions"] for m in (classic_a, bundle))
    row("rated_good", (bundle["ratings"]["good"] >= classic_a["ratings"]["good"] - 1) if rated else None,
        f"bundle {bundle['ratings']['good']} vs classic {classic_a['ratings']['good']} rated good")
    row("judged_balance", bal <= band, f"classic-over-bundle balance {bal}, classic A-vs-B band {band}")
    row("no_question_worse_than_both", not worse_both, f"materially worse than both classic runs: {worse_both}")
    for name, key, rule in (("commands", "commands_median", "lower"), ("latency", "duration_s_median", "lower"),
                            ("cost", "cost_usd_median", "not_higher")):
        a, b = classic_a.get(key), bundle.get(key)
        ok = None if a is None or b is None else (b < a if rule == "lower" else b <= a)
        row(name, ok, f"bundle {b} vs classic {a} ({rule})")
    ra, rb = classic_a.get("answer_reach"), bundle.get("answer_reach")
    if ra and rb:  # no row at all when unmeasured: Task 4's `not r["ok"]` test would count a None row as a failure
        row("answer_reach", rb["reached"] >= ra["reached"],
            f"bundle {rb['reached']} vs classic {ra['reached']} expected documents reached")
    failed = [r for r in rows if r["ok"] is False]
    reasons = [r["detail"] if r["row"] != "no_question_worse_than_both"
               else f"questions {worse_both} are materially worse than both classic runs" for r in failed]
    return {"passed": not failed, "band": band, "balance": bal, "worse_than_both": worse_both,
            "rows": rows, "reasons": reasons}
