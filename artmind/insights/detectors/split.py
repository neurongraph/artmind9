"""split_signal: a probable duplicate whose halves show opposite findings (spec §7).

When one real thing is extracted under two names, its signal splits. Then:
- one half "fades" while the other "rises";
- one half goes with better days while the other goes with worse ones.
Such pairs are the highest-priority curation.
"""

from __future__ import annotations

from artmind.insights.detectors import Detector, RunContext, subject
from artmind.insights.model import Finding

SIGNALS = ("salience_trend", "outcome_lift")


def _plan(ctx: RunContext):
    # Always runs: "no candidates this run" is a real answer, so earlier split
    # findings must go `gone` rather than be carried over as a Skip would.
    return ["all"]


def _run(ctx: RunContext, _target: str) -> list[Finding]:
    by_subject = {
        (f.detector, f.subjects[0]["_id"]): f
        for f in ctx.memo.get("findings", []) if f.detector in SIGNALS and f.subjects
    }
    entities = {e._id: e for e in ctx.entities}
    out = []
    for c in ctx.memo.get("same_as", []):
        a, b = c["canonical"], c["alias"]
        if a not in entities or b not in entities:
            continue
        for signal in SIGNALS:
            fa, fb = by_subject.get((signal, a)), by_subject.get((signal, b))
            if not (fa and fb) or fa.stats.get("direction") == fb.stats.get("direction"):
                continue
            lo, hi = sorted((a, b))
            out.append(Finding(
                key=f"split_signal:{c['class']}:{lo}:{hi}:{signal}", detector="split_signal", kind="health",
                score=round(min(1.0, c["score"] * 1.2), 4),
                title=f"{entities[a].name} and {entities[b].name} look like one {c['class']} "
                      f"but show opposite {signal.replace('_', ' ')}",
                subjects=[subject(entities[a]), subject(entities[b])],
                stats={"canonical": a, "alias": b, "same_as_score": c["score"], "signal": signal,
                       "directions": {a: fa.stats["direction"], b: fb.stats["direction"]}, "headline": c["score"]},
                card={"type": "stat"}, action="curate",
            ))
    return out


SPLIT_SIGNAL = Detector("split_signal", "health", _plan, _run)
