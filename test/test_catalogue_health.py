"""The `health` catalogue part and its background refresh (spec 2026-11-02 §4)."""
import threading
from pathlib import Path

import pytest

from artmind import graph_query
from artmind.catalogue import cache, health, parts, refresh


class HealthGraph:
    """Answers the catalogue's reads with synthetic rows."""

    def __init__(self):
        self.calls = []
        self.twin_rows = [{"domain": "journal", "entity_class": "PERSON", "name": f"ada {i}", "entities": 2}
                          for i in range(14)]
        self.fail_conflicts = False

    def __call__(self, cypher, params):
        self.calls.append((cypher, params))
        if "DomainState" in cypher:
            return [{"domain": d, "generation": 1, "entities": 0, "documents": 0, "history": 0}
                    for d in params["domains"]]
        if "entities > 1" in cypher:
            return self.twin_rows
        if "SameAsProposal" in cypher:
            return [{"id": "p1", "canonical": "e|PERSON|journal", "members": ["e|PERSON|journal", "f|PERSON|journal"]},
                    {"id": "p2", "canonical": "x|PERSON|other", "members": ["x|PERSON|other", "y|PERSON|other"]}]
        if "(co:Conflict)" in cypher:
            if self.fail_conflicts:
                raise RuntimeError("boom")
            return [{"id": "c1", "domain": "journal"}]
        if "NOT EXISTS" in cypher:
            return [{"id": "d1", "name": "a.md", "domain": "journal"}]
        return []

    def count(self, marker):
        return sum(1 for c, _ in self.calls if marker in c)


@pytest.fixture
def graph(monkeypatch, tmp_path):
    import paths
    from artmind import projection, staging_audit, vault_sync

    g = HealthGraph()
    monkeypatch.setattr(graph_query, "_run_read_query", g)
    monkeypatch.setattr(health, "_gaps", lambda: {"journal": {"unembedded_entities": 2, "unprojected_keys": 0, "unembedded_passages": 3},
                                                  "other": {"unembedded_entities": 9, "unprojected_keys": 0, "unembedded_passages": 0}})
    monkeypatch.setattr(staging_audit, "empty_extractions",
                        lambda kg, domains=None, **kw: {"notes": [{"path": "n.md", "domain": "journal", "chars": 300}], "scanned": 4})

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(graph_query, "read_session", lambda: _Session())
    monkeypatch.setattr(projection, "status", lambda tx: {"drift": True, "same_as_drift": True, "schema_drift": False})
    monkeypatch.setattr(paths, "ARTMIND_VAULT_DIR", tmp_path)
    monkeypatch.setattr(vault_sync, "status_report", lambda vault: {"stores": {
        "graph": {"docs": 3, "tables": [("journal", "trips"), ("other", "x")]},
        "structured": {"docs": 0, "tables": [("journal", "trips")]}}})
    monkeypatch.delenv("ARTMIND_PLAN_CATALOG", raising=False)
    import shutil

    shutil.rmtree(Path(paths.LOGS_DIR) / "plan-catalog", ignore_errors=True)
    cache.reset()
    refresh.reset()
    yield g
    cache.reset()
    refresh.reset()


def test_every_field_is_built_with_counts_and_capped_samples(graph):
    out = health.build_health("journal")["health"]
    assert set(out) == set(health.FIELDS)
    assert out["twins"]["count"] == 14 and len(out["twins"]["samples"]) == health.SAMPLES_MAX == 10
    assert out["same_as_open"] == {"count": 1, "samples": [
        {"id": "p1", "canonical": "e|PERSON|journal", "members": 2, "domain": "journal"}]}  # the other domain's proposal is not counted
    assert out["conflicts_open"]["count"] == 1 and out["summaries_missing"]["samples"][0]["name"] == "a.md"
    assert out["unembedded"]["unembedded_entities"] == 2 and out["unembedded"]["unembedded_passages"] == 3
    assert out["empty_extractions"]["count"] == 1
    assert out["behind_vault"]["docs"] == 3 and out["behind_vault"]["tables"] == ["journal.trips"]
    assert out["drift"] == {"count": 1, "drift": True, "same_as_drift": True, "schema_drift": False, "samples": []}


def test_the_readers_send_the_domain_scope(graph):
    health.build_health("journal")
    twins = next(p for c, p in graph.calls if "entities > 1" in c)
    assert twins == {"domains": ["journal"]}
    conflicts = next(c for c, _ in graph.calls if "(co:Conflict)" in c)
    assert "co.status" in conflicts


def test_one_failing_source_does_not_hide_the_others(graph):
    graph.fail_conflicts = True
    out = health.build_health("journal")["health"]
    assert out["conflicts_open"]["error"].startswith("RuntimeError")
    assert out["twins"]["count"] == 14


def test_the_ttl_env_var(monkeypatch):
    monkeypatch.delenv("ARTMIND_CATALOGUE_HEALTH_TTL_S", raising=False)
    assert health.health_ttl_s() == 3600.0
    monkeypatch.setenv("ARTMIND_CATALOGUE_HEALTH_TTL_S", "120")
    assert health.health_ttl_s() == 120.0
    monkeypatch.setenv("ARTMIND_CATALOGUE_HEALTH_TTL_S", "soon")
    assert health.health_ttl_s() == 3600.0


def test_get_never_builds_health_inline(graph):
    cats, detail = cache.get_detailed(["journal"], parts=("classes", "health"))
    assert detail["journal"]["health"] == "absent" and cats["journal"].health is None
    assert graph.count("entities > 1") == 0 and graph.count("SameAsProposal") == 0


def test_health_is_served_once_rebuilt_and_follows_its_ttl(graph, monkeypatch):
    cache.rebuild(["journal"], ["health"])
    cats, detail = cache.get_detailed(["journal"], parts=("health",))
    assert detail["journal"]["health"] == "memory" and cats["journal"].health["twins"]["count"] == 14
    assert cache.health_state(cats["journal"]) == "fresh"
    monkeypatch.setenv("ARTMIND_CATALOGUE_HEALTH_TTL_S", "0.001")
    import time

    time.sleep(0.01)
    assert cache.health_state(cats["journal"]) == "stale"
    # reading a stale part still does not build it
    before = graph.count("entities > 1")
    cache.get_detailed(["journal"], parts=("health",))
    assert graph.count("entities > 1") == before


def test_health_survives_a_fresh_process_via_disk(graph):
    cache.rebuild(["journal"], ["health"])
    cache.reset()
    cats, detail = cache.get_detailed(["journal"], parts=("health",))
    assert detail["journal"]["health"] == "disk" and cats["journal"].health["conflicts_open"]["count"] == 1


def test_a_plain_call_starts_no_background_thread(graph):
    before = {t.name for t in threading.enumerate()}
    cache.get_detailed(["journal"], parts=("classes", "health"))
    assert not refresh.enabled()
    assert {t.name for t in threading.enumerate()} == before
    assert refresh.schedule_health(["journal"]) == []


def test_background_refresh_is_debounced_per_domain(graph):
    clock = [1000.0]
    ran = []
    refresh.reset(clock=lambda: clock[0], spawn=lambda fn: ran.append(fn))
    refresh.enable_background()
    assert refresh.schedule_health(["a", "b"]) == ["a", "b"]
    assert refresh.schedule_health(["a"]) == []  # already running
    for fn in ran:
        fn()  # jobs end; the debounce still holds
    assert refresh.schedule_health(["a"]) == []
    clock[0] += refresh.DEBOUNCE_S + 1
    assert refresh.schedule_health(["a"]) == ["a"]


def test_a_changed_generation_schedules_one_refresh_that_builds_health(graph):
    ran = []
    refresh.reset(spawn=lambda fn: ran.append(fn))
    refresh.enable_background()
    cache.get(["journal"])  # first read: nothing cached, health absent -> scheduled
    assert len(ran) == 1
    ran.pop()()
    assert graph.count("entities > 1") == 1
    cats, detail = cache.get_detailed(["journal"], parts=("health",))
    assert detail["journal"]["health"] in ("memory", "disk") and cats["journal"].health is not None


def test_health_has_its_own_part_key():
    assert parts.part_key("health", (1, 0, 0, 0, "")) == ["ttl"]


@pytest.mark.parametrize("fn", ["run_chat_ui", "run_admin_ui"])
def test_the_ui_entry_points_enable_the_background_refresh(fn):
    import inspect

    from artmind.webui import app

    assert "enable_background" in inspect.getsource(getattr(app, fn))


def test_serve_enables_the_background_refresh():
    import inspect

    from artmind import server

    assert "enable_background" in inspect.getsource(server._lifespan)
