"""The daily topic plan check (spec 2026-11-10 §8.3), run by the long-lived servers'
background job (`catalogue/refresh.start_topic_checks`). No LLM unless a plan opted in.

Every plan gets a freshness check (stored under `logs/topic-freshness/`). Only in the admin
console process (`auto_refresh=True`), a plan with `refresh: on_stale` is refreshed after a
check finds it stale, and one with `refresh: weekly` at most once a week when stale, each
only when the dry run's estimate is within ARTMIND_TOPIC_MAX_COST_USD (default 1.0). The
outcome is stored with the check (`auto_refresh`), so the Topics tab can say why.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from loguru import logger

MAX_COST_ENV = "ARTMIND_TOPIC_MAX_COST_USD"
DEFAULT_MAX_COST = 1.0
WEEK = timedelta(days=7)


def max_cost() -> float:
    raw = os.environ.get(MAX_COST_ENV)
    if raw is None:
        return DEFAULT_MAX_COST
    try:
        return max(0.0, float(raw))
    except ValueError:
        logger.warning("topics: {}={!r} is not a number; using {}", MAX_COST_ENV, raw, DEFAULT_MAX_COST)
        return DEFAULT_MAX_COST


def _parse(ts) -> datetime | None:
    try:
        return datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def due(spec, status: str, now: datetime) -> str | None:
    """Why a plan should refresh now, or None."""
    if status != "stale" or spec.refresh == "manual":
        return None
    if spec.refresh == "weekly":
        last = _parse((spec.built or {}).get("at"))
        if last is not None and now - last < WEEK:
            return None
        return "weekly and stale"
    return "on_stale and stale"


def run_once(*, auto_refresh: bool = False, now: datetime | None = None, build_fn=None) -> list[dict]:
    """Check every valid plan; refresh opted-in stale ones when allowed. Returns one record each."""
    from artmind.topics import freshness, plan_store

    now = now or datetime.now(timezone.utc)
    if build_fn is None:
        from artmind.topics.build import build_topic as build_fn
    plans, _ = plan_store.list_plans()
    out = []
    for loaded in plans:
        spec = loaded.spec
        result = freshness.check(loaded, keep=False)
        record = {"slug": spec.slug, "status": result["status"]}
        reason = due(spec, result["status"], now) if auto_refresh else None
        if reason:
            result["auto_refresh"] = _refresh(spec, reason, build_fn, now)
            record["auto_refresh"] = result["auto_refresh"]["status"]
        try:
            freshness.store(spec.slug, result)
        except OSError:
            pass
        out.append(record)
    return out


def _refresh(spec, reason: str, build_fn, now: datetime) -> dict:
    at = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    from artmind.topics.build import args_from_plan

    args = {**args_from_plan(spec), "slug": spec.slug}
    try:
        plan = build_fn(dry_run=True, **args)
        cost = (plan.get("effect") or {}).get("llm", {}).get("est_cost_usd") or 0.0
        limit = max_cost()
        if cost > limit:
            return {"at": at, "status": "skipped", "reason": f"estimated ${cost:.2f} is above {MAX_COST_ENV}={limit:.2f}"}
        done = build_fn(dry_run=False, **args)
        if done.get("error"):
            return {"at": at, "status": "failed", "reason": done["error"]}
        return {"at": at, "status": "refreshed", "reason": reason, "est_cost_usd": cost,
                "calls": (done.get("calls") or {}).get("made")}
    except Exception as exc:
        logger.exception("topics: automatic refresh of {} failed", spec.slug)
        return {"at": at, "status": "failed", "reason": str(exc)}
