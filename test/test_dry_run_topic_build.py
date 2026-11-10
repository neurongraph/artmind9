"""`topic build --dryRun` (tier 1): only read Cypher (the keyword counts and footnote links),
no LLM call, no page or plan; the effect matches the real run's."""
import json
import re

import pytest
from click.testing import CliRunner

import _topic_world as w
from _dry_run import assert_effect_shape
from artmind.cli import cli

_WRITE = re.compile(r"\b(MERGE|CREATE|SET|DELETE|REMOVE|DROP)\b")


@pytest.fixture
def world(tmp_path, monkeypatch):
    import paths

    monkeypatch.setattr(paths, "DOMAIN_TOPIC_PLANS_DIR", tmp_path / "plans")
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr("artmind.vector_query._expand", lambda d: list(d))
    monkeypatch.setattr("artmind.topics.shape.facts", lambda domains, cards=None: {
        "documents": 100, "dated_from_content": 100, "months": 40})
    monkeypatch.setattr("artmind.survey.survey_search", w.search)
    monkeypatch.setattr("artmind.topics.build._generations", lambda d, f: {"j": 1})
    monkeypatch.setenv("ARTMIND_TOPIC_MAP_CONCURRENCY", "1")
    return tmp_path


def _invoke(tmp_path, monkeypatch, *extra):
    session, factory = w.session_factory()
    llms = w.LLMs()
    monkeypatch.setattr("artmind.graph_query.read_session", factory)
    monkeypatch.setattr("artmind.topics.build._default_llms", lambda model: (llms.map, llms.reduce))
    result = CliRunner().invoke(cli, ["topic", "build", "Garden work", "--domain", "j", "--facet", "tomato planting",
                                      "--facet", "compost heap", "--out", str(tmp_path / "_Topics"), "--model", "m",
                                      *extra, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout), llms, session


def test_dry_run_reads_only_and_matches_the_real_effect(world, monkeypatch):
    dry, llms, session = _invoke(world, monkeypatch, "--dryRun")
    assert llms.maps == [] and llms.reduces == []
    assert not (world / "_Topics").exists() and not (world / "plans").exists()
    assert all(not _WRITE.search(c) for c, _ in session.calls)
    counts = [p for c, p in session.calls if "passage_text_ft" in c]
    assert [p["ft_query"] for p in counts] == [w.ft("tomato planting"), w.ft("compost heap")]
    assert all(p["domains"] == ["j"] for p in counts)
    assert dry["dry_run"] is True and dry["command"] == "topic build"
    assert_effect_shape(dry["effect"], llm_calls=7)
    assert dry["effect"]["changes"] == [{"kind": "topic_write", "count": 2,
                                         "sample": ["garden-work.md", "topic_plans/garden-work"]}]
    real, llms, _ = _invoke(world, monkeypatch)
    assert "dry_run" not in real and real["effect"] == dry["effect"]
    assert len(llms.maps) == 4 and (world / "_Topics" / "garden-work.md").exists()
    assert (world / "plans" / "garden-work" / "plan.yaml").exists()
