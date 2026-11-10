"""`topic refresh --dryRun` (tier 1): rebuilds from the plan, reads only, no LLM call; the
effect matches the real run's."""
import json
import re

import pytest
from click.testing import CliRunner

import _topic_world as w
from _dry_run import assert_effect_shape
from artmind.cli import cli
from artmind.topics import build

_WRITE = re.compile(r"\b(MERGE|CREATE|SET|DELETE|REMOVE|DROP)\b")


@pytest.fixture
def world(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_TOPIC_PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr("artmind.vector_query._expand", lambda d: list(d))
    monkeypatch.setattr("artmind.survey.survey_search", w.search)
    monkeypatch.setattr("artmind.topics.build._generations", lambda d, f: {"j": 1})
    monkeypatch.setenv("ARTMIND_TOPIC_MAP_CONCURRENCY", "1")
    session, factory = w.session_factory()
    llms = w.LLMs()
    build.build_topic("Garden work", ["j"], facets=["tomato planting", "compost heap"], by="year", split="none",
                      out=tmp_path / "_Topics", model="m", session_factory=factory, search=w.search,
                      map_llm=llms.map, reduce_llm=llms.reduce, generations={"j": 1})
    return tmp_path


def _invoke(tmp_path, monkeypatch, *extra):
    session, factory = w.session_factory({"D3": "v-new"})
    llms = w.LLMs()
    monkeypatch.setattr("artmind.graph_query.read_session", factory)
    monkeypatch.setattr("artmind.topics.build._default_llms", lambda model: (llms.map, llms.reduce))
    result = CliRunner().invoke(cli, ["topic", "refresh", "garden-work", "--out", str(tmp_path / "_Topics"),
                                      *extra, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout), llms, session


def test_dry_run_rebuilds_nothing_and_matches_the_real_effect(world, monkeypatch):
    before = (world / "_Topics" / "garden-work.md").read_bytes()
    dry, llms, session = _invoke(world, monkeypatch, "--dryRun")
    assert llms.maps == [] and (world / "_Topics" / "garden-work.md").read_bytes() == before
    assert all(not _WRITE.search(c) for c, _ in session.calls)
    assert dry["dry_run"] is True
    # only D3 changed: one map call (the cache holds the rest) plus three reduces
    assert dry["refreshed"][0]["calls"] == {"map": 1, "map_cached": 3, "reduce": 3}
    assert_effect_shape(dry["effect"], llm_calls=4)
    assert dry["effect"]["changes"] == [{"kind": "topic_write", "count": 2, "sample": ["garden-work"]}]
    real, llms, _ = _invoke(world, monkeypatch)
    assert len(llms.maps) == 1 and real["effect"] == dry["effect"]
    assert (world / "_Topics" / "garden-work.md").read_bytes() != before


def test_refresh_of_a_page_without_a_plan_uses_its_topic(world, monkeypatch):
    from artmind.topics import store

    store.write_page(world / "_Topics", "old-page", {"artmind_topic": 1, "topic": "tomato planting", "slug": "old-page",
                                                     "domains": ["j"], "built": "2026-11-01", "sources": []}, "x")
    monkeypatch.setattr("artmind.topics.shape.facts", lambda domains, cards=None: {})
    session, factory = w.session_factory()
    monkeypatch.setattr("artmind.graph_query.read_session", factory)
    r = CliRunner().invoke(cli, ["topic", "refresh", "old-page", "--out", str(world / "_Topics"), "--dryRun",
                                 "--compact"])
    assert r.exit_code == 0, r.output
    out = json.loads(r.stdout)["refreshed"][0]
    assert out["subjects"] == [{"n": 1, "text": "tomato planting"}] and out["grid"]["slices"]["by"] == "none"
