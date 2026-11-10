"""The daily topic plan check (spec 2026-11-10 §8.3): every plan checked, only opted-in stale
plans refreshed, only in the admin console, never above ARTMIND_TOPIC_MAX_COST_USD."""
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from artmind.topics import freshness, plan_store, schedule
from artmind.topics import plan as model

NOW = datetime(2026, 11, 10, tzinfo=timezone.utc)


@pytest.fixture
def plans(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_TOPIC_PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")

    def save(slug, refresh, at="2026-11-01T00:00:00Z"):
        spec = model.PlanSpec(slug=slug, topic=slug, version=1, domains=("j",), subjects=("x",), refresh=refresh)
        skill = model.default_skill_md(spec)
        plan_store.write_plan(replace(spec, built={"at": at, "generations": {"j": 1}, "grid": [], "model": "m",
                                                   "version": 1, "fingerprint": model.fingerprint(spec, skill)}), skill)
    return save


class _Builds:
    def __init__(self, cost=0.5):
        self.calls, self.cost = [], cost

    def __call__(self, dry_run, **kw):
        self.calls.append((kw["slug"], dry_run, kw["model"]))
        return {"effect": {"llm": {"est_cost_usd": self.cost}}, "calls": {"made": 3}}


def _stale(monkeypatch):
    monkeypatch.setattr(freshness, "check", lambda loaded, session=None, keep=True: {"status": "stale", "reasons": ["1 new"]})


def test_only_opted_in_stale_plans_refresh_and_only_with_auto_refresh(plans, monkeypatch):
    plans("a-manual", "manual")
    plans("b-on-stale", "on_stale")
    _stale(monkeypatch)
    builds = _Builds()
    assert schedule.run_once(auto_refresh=False, now=NOW, build_fn=builds) == [
        {"slug": "a-manual", "status": "stale"}, {"slug": "b-on-stale", "status": "stale"}]
    assert builds.calls == []
    out = schedule.run_once(auto_refresh=True, now=NOW, build_fn=builds)
    assert out[1] == {"slug": "b-on-stale", "status": "stale", "auto_refresh": "refreshed"}
    assert builds.calls == [("b-on-stale", True, "m"), ("b-on-stale", False, "m")]  # dry run, then the build
    assert freshness.stored("b-on-stale")["auto_refresh"]["status"] == "refreshed"


def test_a_refresh_above_the_cost_cap_is_skipped_and_says_why(plans, monkeypatch):
    plans("b-on-stale", "on_stale")
    _stale(monkeypatch)
    monkeypatch.setenv("ARTMIND_TOPIC_MAX_COST_USD", "0.25")
    builds = _Builds(cost=0.5)
    schedule.run_once(auto_refresh=True, now=NOW, build_fn=builds)
    assert builds.calls == [("b-on-stale", True, "m")]
    stored = freshness.stored("b-on-stale")["auto_refresh"]
    assert stored["status"] == "skipped" and "ARTMIND_TOPIC_MAX_COST_USD=0.25" in stored["reason"]


def test_weekly_refreshes_at_most_once_a_week(plans, monkeypatch):
    plans("w-recent", "weekly", at="2026-11-08T00:00:00Z")
    plans("w-old", "weekly", at="2026-10-01T00:00:00Z")
    _stale(monkeypatch)
    builds = _Builds()
    out = schedule.run_once(auto_refresh=True, now=NOW, build_fn=builds)
    assert {r["slug"]: r.get("auto_refresh") for r in out} == {"w-old": "refreshed", "w-recent": None}


def test_a_fresh_plan_is_never_refreshed(plans, monkeypatch):
    plans("b-on-stale", "on_stale")
    monkeypatch.setattr(freshness, "check", lambda loaded, session=None, keep=True: {"status": "fresh"})
    builds = _Builds()
    schedule.run_once(auto_refresh=True, now=NOW, build_fn=builds)
    assert builds.calls == []
