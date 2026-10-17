"""salience_trend / type_trend: rising and fading entities and occurrent types (spec §6.1)."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict

from artmind.insights.detectors import Detector, RunContext, Skip, subject
from artmind.insights.model import Finding
from artmind.insights.stats import period_of, rate_per_100, support_weight, theil_sen_slope

SMOOTH = 0.5  # added to both sides of the ratio so a zero period cannot divide by zero


def _series(days, periods: list[str], period: str, docs: dict[str, int]) -> tuple[list[float], int]:
    counts = Counter(period_of(d, period) for d in days)
    rates = [round(rate_per_100(counts.get(p, 0), docs[p]), 3) for p in periods]
    return rates, sum(counts.get(p, 0) for p in periods)


def _trend(rates: list[float], support: int, cfg: dict) -> dict | None:
    if support < cfg.get("min_support", 8) or len(rates) < 2:
        return None
    ratio = (rates[-1] + SMOOTH) / (statistics.mean(rates[:-1]) + SMOOTH)
    log_ratio = math.log2(ratio)
    if abs(log_ratio) < cfg.get("min_log_ratio", 0.58):
        return None
    slope = theil_sen_slope(rates[-cfg.get("window", 3):])
    if slope == 0 or (slope > 0) != (log_ratio > 0):
        return None
    return {
        "direction": "rising" if log_ratio > 0 else "fading",
        "ratio": round(ratio, 3), "slope": round(slope, 3), "support": support, "headline": round(ratio, 3),
        "score": round(min(1.0, abs(log_ratio) / 3 * support_weight(support)), 4),
    }


def _temporal_skip(ctx: RunContext) -> Skip | None:
    if not ctx.temporal():
        return Skip("no document dates (valid time mostly defaulted)")
    if len(ctx.scored_periods()) < 2:
        return Skip(f"fewer than 2 periods with >= {ctx.config.min_period_docs} documents")
    return None


def _card(periods, rates) -> dict:
    return {"type": "series", "periods": periods, "values": rates, "unit": "per 100 documents"}


def _plan_salience(ctx: RunContext):
    skip = _temporal_skip(ctx)
    if skip:
        return skip
    return ctx.classes_of_kind("recurrent") or Skip("no recurrent classes")


def _run_salience(ctx: RunContext, cls: str) -> list[Finding]:
    cfg, periods = ctx.config.detector("salience_trend"), ctx.scored_periods()
    out = []
    for e in ctx.members(cls):
        rates, support = _series(e.days, periods, ctx.config.period, ctx.profile.docs_per_period)
        st = _trend(rates, support, cfg)
        if st is None:
            continue
        score = st.pop("score")
        out.append(Finding(
            key=f"salience_trend:{cls}:{e._id}", detector="salience_trend", kind="corpus", score=score,
            title=f"{st['direction'].capitalize()}: {e.name}", subjects=[subject(e)],
            stats={**st, "rates": dict(zip(periods, rates))}, card=_card(periods, rates),
        ))
    return out


def _plan_types(ctx: RunContext):
    skip = _temporal_skip(ctx)
    if skip:
        return skip
    classes = [c for c in ctx.classes_of_kind("occurrent") if ctx.profile.classes[c].type_counts]
    return classes or Skip("no occurrent classes with a type")


def _run_types(ctx: RunContext, cls: str) -> list[Finding]:
    cfg, periods = ctx.config.detector("type_trend"), ctx.scored_periods()
    by_type: dict[str, set[str]] = defaultdict(set)
    for e in ctx.entities:
        if e.cls == cls and e.type and e.type.strip() and e._id not in ctx.anchors:
            by_type[e.type.strip().lower()].update(e.days)
    out = []
    for t in sorted(by_type):
        rates, support = _series(by_type[t], periods, ctx.config.period, ctx.profile.docs_per_period)
        st = _trend(rates, support, cfg)
        if st is None:
            continue
        score = st.pop("score")
        out.append(Finding(
            key=f"type_trend:{cls}:{t}", detector="type_trend", kind="corpus", score=score,
            title=f"{st['direction'].capitalize()}: {t} ({cls})", subjects=[],
            stats={**st, "type": t, "class": cls, "rates": dict(zip(periods, rates))}, card=_card(periods, rates),
        ))
    return out


SALIENCE_TREND = Detector("salience_trend", "corpus", _plan_salience, _run_salience)
TYPE_TREND = Detector("type_trend", "corpus", _plan_types, _run_types)
