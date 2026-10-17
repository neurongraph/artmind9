"""dormancy: entities gone quiet relative to their own rhythm (spec §6.2).

`as_of` is the latest document date in the domain, not today, so a vault
that has not been ingested lately does not make everyone look dormant.
"""

from __future__ import annotations

import math

from artmind.insights.detectors import Detector, RunContext, Skip, subject
from artmind.insights.model import Finding
from artmind.insights.stats import days_between, median_gap_days, support_weight


def _plan(ctx: RunContext):
    if not ctx.temporal() or ctx.as_of is None:
        return Skip("no document dates (valid time mostly defaulted)")
    return ctx.classes_of_kind("recurrent") or Skip("no recurrent classes")


def _run(ctx: RunContext, cls: str) -> list[Finding]:
    cfg = ctx.config.detector("dormancy")
    multiple, min_silence = cfg.get("silence_multiple", 3), cfg.get("min_silence_days", 60)
    out = []
    for e in ctx.members(cls):
        cadence = median_gap_days(e.days)
        if cadence <= 0:
            continue
        silence = days_between(e.days[-1], ctx.as_of)
        if silence <= multiple * cadence or silence <= min_silence:
            continue
        score = min(1.0, math.log(silence / cadence) / math.log(30)) * support_weight(len(e.days))
        out.append(Finding(
            key=f"dormancy:{cls}:{e._id}", detector="dormancy", kind="corpus", score=round(score, 4),
            title=f"Quiet: {e.name}", subjects=[subject(e)],
            stats={"last_seen": e.days[-1], "cadence_days": cadence, "silence_days": silence,
                   "support": len(e.days), "headline": silence},
            card={"type": "ranking", "columns": ["last_seen", "cadence_days", "silence_days"]},
        ))
    return out


DORMANCY = Detector("dormancy", "corpus", _plan, _run)
