"""`topic build --dryRun`: no LLM call, no page; the effect matches the real run."""
import json
from unittest.mock import patch

from click.testing import CliRunner

from _dry_run import assert_effect_shape
from artmind.cli import cli
from artmind.topics import build, store
from artmind.topics.gather import Material, Source

LINKS = {"c1_p01": {"path": "p/one.md", "title": "One", "header_path": "Terms", "doc_id": "D1", "name": "one.md",
                    "version": "v1"}}


def _materials():
    return [Material("D1", "One", "one.md", "j", "S1", [Source("c1_p01", "c1", "Notice is 30 days.")])]


def _map(prompt):
    return {"relevant": True, "notes": [{"claim": "Notice is 30 days.", "passages": ["c1_p01"]}]}


def _reduce(prompt):
    return "## Overview\n\nNotice is 30 days [#c1_p01]."


def _gather(*args, **kwargs):
    return _materials(), LINKS


def _invoke(tmp_path, *extra):
    calls = []

    def map_llm(prompt):
        calls.append("map")
        return _map(prompt)

    def reduce_llm(prompt):
        calls.append("reduce")
        return _reduce(prompt)

    with patch("artmind.topics.build.gather.gather", _gather), \
         patch("artmind.topics.build._default_llms", lambda model: (map_llm, reduce_llm)), \
         patch("artmind.topics.build.anchor_mod.choose_anchor", lambda *a, **k: {"kind": "topic"}), \
         patch("artmind.topics.build._graph_domains", lambda d: []):
        result = CliRunner().invoke(cli, ["topic", "build", "Notice", "--domain", "j", "--out", str(tmp_path),
                                          "--model", "m", *extra, "--compact"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout), calls


def test_dry_run_makes_no_call_writes_no_page_and_matches_the_real_effect(tmp_path):
    dry, calls = _invoke(tmp_path, "--dryRun")

    assert calls == [] and not list(tmp_path.iterdir())
    assert dry["dry_run"] is True
    assert_effect_shape(dry["effect"], llm_calls=2)
    assert dry["effect"]["llm"]["model"] == "m"
    assert dry["effect"]["changes"] == [{"kind": "topic_write", "count": 1, "sample": ["notice"]}]
    assert dry["effect"]["records"] == [str(tmp_path / "notice.md")]

    real, calls = _invoke(tmp_path)

    assert calls == ["map", "reduce"] and (tmp_path / "notice.md").exists()
    assert real["dry_run"] is False
    assert real["effect"] == dry["effect"]


def test_a_build_that_finds_nothing_reports_no_page(tmp_path):
    with patch("artmind.topics.build.gather.gather", _gather), \
         patch("artmind.topics.build._default_llms",
               lambda model: (lambda p: {"relevant": False, "notes": []}, _reduce)), \
         patch("artmind.topics.build.anchor_mod.choose_anchor", lambda *a, **k: {"kind": "topic"}), \
         patch("artmind.topics.build._graph_domains", lambda d: []):
        result = CliRunner().invoke(cli, ["topic", "build", "Notice", "--domain", "j", "--out", str(tmp_path),
                                          "--model", "m", "--compact"])

    payload = json.loads(result.stdout)
    assert payload["effect"]["changes"] == [] and payload["effect"]["records"] == []
    assert not list(tmp_path.iterdir())
